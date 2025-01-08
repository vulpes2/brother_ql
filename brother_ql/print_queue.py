import logging
from collections import deque
import time

from brother_ql.reader import interpret_response
from brother_ql.conversion import queue_convert
from brother_ql.raster import BrotherQLRaster
from brother_ql.backends import BrotherQLBackendGeneric


logger = logging.getLogger(__name__)


class BrotherPrintQueue:
    def __init__(self, printer: BrotherQLBackendGeneric, rasterizer: BrotherQLRaster):
        self._print_queue = deque()
        self._qlr = rasterizer
        self._printer = printer
        self._printing = False
        self.status = None
        self.ready = False
        self.last_error = []
        self.page_count = 0
        self.initialize()

    def clear(self):
        self._print_queue.clear()

    def initialize(self):
        self._qlr.clear()

        # Standard init sequence
        # Invalidate -> Initialize -> Status Request
        self._qlr.add_invalidate()
        self._printer.write(self._qlr.data)
        self._qlr.clear()

        self._qlr.add_initialize()
        self._printer.write(self._qlr.data)
        self._qlr.clear()

        ready = self._validate_status(phase=0, request=True, timeout=2)
        self._printing = False
        self.ready = ready

        self.page_count = len(self._print_queue)

    def queue_image(self, **kwargs):
        if self._printing:
            raise RuntimeError("Can't queue images while printing")
        # BrotherQLRaster page number starts from 0
        self._qlr.page_number = len(self._print_queue) - 1
        page_data = queue_convert(qlr=self._qlr, **kwargs)
        for p in page_data:
            self._print_queue.append(p)
            logger.debug(
                # f"Queued page block {' '.join( map(lambda byte: f'{byte:02X}', p))}"
                f"Queued page block of {len(p)} bytes"
            )
        self.page_count = len(self._print_queue)

    def submit(self, clear_on_failure: bool = False):
        if not self.ready:
            logger.debug("Printing has failed previously, initializing printer")
            self.initialize()
        self._printing = True
        count = 0
        qsize = len(self._print_queue)
        logger.info(f"Submitting print queue with {qsize} pages")
        completed = False
        while len(self._print_queue) > 0:
            logger.info(f"Submitting page {count+1} of {qsize}")
            printed = self._submit_page()
            count += 1
            if not printed:
                logger.warning("Page submission failed, giving up")
                break

        logger.debug("Queue processing complete")
        remaining_pages = len(self._print_queue)

        if remaining_pages != 0:
            logger.debug(f"There are {remaining_pages} pages remaining in the queue")
            if clear_on_failure:
                logger.debug(f"Clearing queue with failed {remaining_pages} pages")
                self._print_queue.clear()
        else:
            completed = True
        self.ready = self._validate_status(phase=0, request=True, timeout=2)
        if self.ready:
            logger.debug("Printer is ready to receive data")
        else:
            logger.error("Printer is not ready")
            logger.debug(self.status)

        self._printing = False
        return completed

    def _submit_page(self):
        page_data = self._print_queue[0]
        completed = False

        while not completed:
            logger.debug(
                f"Command data: {' '.join(map(lambda byte: f'{byte:02X}', page_data))}"
            )
            self._printer.write(page_data)

            sts_started = False
            sts_printed = False
            sts_ready = False
            logger.debug("Waiting for the printer to start printing")
            sts_started = self._validate_status(status=6, phase=1, timeout=2)
            if sts_started:
                logger.debug("Printing started, waiting for completion")
                sts_printed = self._validate_status(status=1, phase=1)
                if sts_printed:
                    logger.debug("Printing completed, waiting for ready status")
                    sts_ready = self._validate_status(status=6, phase=0, timeout=2)
                    if sts_ready:
                        logger.debug("Printer is ready to receive data")
                else:
                    logger.warning("Printing started but did not finish")
            else:
                logger.warning("Printing failed to start")

            completed = sts_started and sts_printed and sts_ready
            if completed:
                # Remove printed page
                self._print_queue.popleft()
            else:
                break

        self.page_count = len(self._print_queue)
        return completed

    def _validate_status(
        self,
        status: int = -1,
        phase: int = -1,
        timeout: int = 10,
        request: bool = False,
    ):
        if request:
            if status != -1:
                raise AttributeError(
                    "Specifying an expected status type is not allowed for requests"
                )
            status = 0  # always 0x00 for requests
            logger.debug("Requesting status")
            self._qlr.clear()
            self._qlr.add_status_information()
            self._printer.write(self._qlr.data)
            self._qlr.clear()

        logger.debug("Waiting for response")
        if status != -1 or phase != -1:
            logger.debug(
                f"Expecting status type 0x{status:02X}, phase type 0x{phase:02X}, timeout {timeout}s"
            )

        start_time = time.time()
        response = None
        while time.time() - start_time < timeout:
            data = self._printer.read()
            if not data:
                time.sleep(0.1)
                continue
            try:
                response = interpret_response(data)
                break
            except ValueError:
                continue

        match = False
        if response is not None:
            self.status = response
            if len(response["errors"]) > 0:
                self.last_error = response["errors"]
            status_match = status == -1 or status == response["status_code"]
            phase_match = phase == -1 or phase == response["phase_code"]
            match = status_match and phase_match
            logger.debug(
                f"Got status 0x{response['status_code']:02X}, phase 0x{response['phase_code']:02X}"
            )
            if match:
                logger.debug("Response matches expected status")
            else:
                logger.debug("Response does not match expected status")
        else:
            logger.warning(
                f"Did not receive a response from printer within {timeout} seconds"
            )

        return match
