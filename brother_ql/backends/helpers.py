#!/usr/bin/env python

"""
Helpers for the subpackage brother_ql.backends

* device discovery
* printing
"""

import logging, time

from brother_ql.backends import backend_factory, guess_backend
from brother_ql.reader import interpret_response

logger = logging.getLogger(__name__)

def discover(backend_identifier='linux_kernel'):
    if backend_identifier is None:
        logger.info("Backend for discovery not specified, defaulting to linux_kernel.")
        backend_identifier = "linux_kernel"
    be = backend_factory(backend_identifier)
    list_available_devices = be['list_available_devices']
    BrotherQLBackend       = be['backend_class']
    available_devices = list_available_devices()
    return available_devices

def send(instructions, printer_identifier=None, backend_identifier=None, blocking=True):
    """
    Send instruction bytes to a printer.

    :param bytes instructions: The instructions to be sent to the printer.
    :param str printer_identifier: Identifier for the printer.
    :param str backend_identifier: Can enforce the use of a specific backend.
    :param bool blocking: Indicates whether the function call should block while waiting for the completion of the printing.
    """

    status = {
      'instructions_sent': True, # The instructions were sent to the printer.
      'outcome': 'unknown', # String description of the outcome of the sending operation like: 'unknown', 'sent', 'printed', 'error'
      'printer_state': None, # If the selected backend supports reading back the printer state, this key will contain it.
      'did_print': False, # If True, a print was produced. It defaults to False if the outcome is uncertain (due to a backend without read-back capability).
      'ready_for_next_job': False, # If True, the printer is ready to receive the next instructions. It defaults to False if the state is unknown.
    }
    selected_backend = None
    if backend_identifier:
        selected_backend = backend_identifier
    else:
        try:
            selected_backend = guess_backend(printer_identifier)
        except:
            logger.info("No backend stated. Selecting the default linux_kernel backend.")
            selected_backend = 'linux_kernel'

    be = backend_factory(selected_backend)
    list_available_devices = be['list_available_devices']
    BrotherQLBackend       = be['backend_class']

    printer = BrotherQLBackend(printer_identifier)

    start = time.time()
    logger.info('Sending instructions to the printer. Total: %d bytes.', len(instructions))
    printer.write(instructions)
    status['outcome'] = 'sent'

    if not blocking:
        return status
    if selected_backend == 'network':
        """ No need to wait for completion. The network backend doesn't support readback. """
        return status

    while time.time() - start < 10:
        data = printer.read()
        if not data:
            time.sleep(0.005)
            continue
        try:
            result = interpret_response(data)
        except ValueError:
            logger.error("TIME %.3f - Couln't understand response: %s", time.time()-start, data)
            continue
        status['printer_state'] = result
        logger.debug('TIME %.3f - result: %s', time.time()-start, result)
        if result['errors']:
            logger.error('Errors occured: %s', result['errors'])
            status['outcome'] = 'error'
            break
        if result['status_type'] == 'Printing completed':
            status['did_print'] = True
            status['outcome'] = 'printed'
        if result['status_type'] == 'Phase change' and result['phase_type'] == 'Waiting to receive':
            status['ready_for_next_job'] = True
        if status['did_print'] and status['ready_for_next_job']:
            break

    if not status['did_print']:
        logger.warning("'printing completed' status not received.")
    if not status['ready_for_next_job']:
        logger.warning("'waiting to receive' status not received.")
    if (not status['did_print']) or (not status['ready_for_next_job']):
        logger.warning('Printing potentially not successful?')
    if status['did_print'] and status['ready_for_next_job']:
        logger.info("Printing was successful. Waiting for the next job.")

    return status

def get_printer(
    printer_identifier=None,
    backend_identifier=None,
):
    """
    Instantiate a printer object for communication. Only bidirectional transport backends are supported.

    :param str printer_identifier: Identifier for the printer.
    :param str backend_identifier: Can enforce the use of a specific backend.
    """

    selected_backend = None
    if backend_identifier:
        selected_backend = backend_identifier
    else:
        try:
            selected_backend = guess_backend(printer_identifier)
        except ValueError:
            logger.info("No backend stated. Selecting the default linux_kernel backend.")
            selected_backend = "linux_kernel"
    if selected_backend == "network":
        # Not implemented due to lack of an available test device
        raise NotImplementedError

    be = backend_factory(selected_backend)
    BrotherQLBackend = be["backend_class"]
    printer = BrotherQLBackend(printer_identifier)
    return printer

def status(
    printer_identifier=None,
    backend_identifier=None,
):
    """
    Retrieve status info from the printer, including model and currently loaded media size.

    """

    printer=get_printer(printer_identifier, backend_identifier)

    logger.info("Sending status information request to the printer.")
    printer.write(b"\x1b\x69\x53")  # "ESC i S" Status information request
    data = printer.read()
    try:
        result = interpret_response(data)
    except ValueError:
        logger.error("Failed to parse response data: %s", data)

    logger.info(f"Printer Series Code: 0x{result['series_code']:02x}")
    logger.info(f"Printer Model Code: 0x{result['model_code']:02x}")
    logger.info(f"Printer Status Type: {result['status_type']} ")
    logger.info(f"Printer Phase Type: {result['phase_type']})")
    logger.info(f"Printer Errors: {result['errors']}")
    logger.info(f"Media Type: {result['media_type']}")
    logger.info(f"Media Size: {result['media_width']} x {result['media_length']} mm")

    return result

def configure(
    printer_identifier=None,
    backend_identifier=None,
    write=False,
    auto_power_off=None,
    auto_power_on=None,
):
    """
    Read or modify power settings.

    :param bool write: Write configuration to printer
    :param int auto_power_off: multiples of 10 minutes for power off, 0 means disabled
    :param bool auto_power_on: whether to enable auto power on or not, 1 means enabled, 0 means disabled
    """
    printer=get_printer(printer_identifier, backend_identifier)

    if write:
        if auto_power_off is None or auto_power_on is None:
            raise ValueError("You must provide the settings values")
        if auto_power_off < 0 or auto_power_off > 6:
            raise ValueError("Auto power off can only be set between 0 to 6")
        if type(auto_power_on) is not bool:
            raise ValueError("Auto power on is a boolean setting")

    printer.write(b"\x1b\x69\x53")  # "ESC i S" Status information request
    data = printer.read()
    result = interpret_response(data)
    if result['status_type'] != 'Reply to status request':
        raise ValueError

    if write:
        # change auto power on settings
        printer.write(b"\x1b\x69\x61\x01") # Switch to raster command mode
        # 0x1b 0x69 0x55 settings
        # 0x70 auto power on
        # 0x00 write
        # bool state
        command = b"\x1b\x69\x55\x70\x00" + auto_power_on.to_bytes(1)
        printer.write(command)

        # retrieve status to make sure no errors occured
        printer.write(b"\x1b\x69\x53")  # "ESC i S" Status information request
        data = printer.read()
        result = interpret_response(data)
        if result['status_type'] != 'Reply to status request':
            raise ValueError("Failed to modify settings")

    # read auto power on settings
    printer.write(b"\x1b\x69\x61\x01") # Switch to raster command mode
    # 0x1b 0x69 0x55 settings
    # 0x70 auto power on
    # 0x01 read
    printer.write(b"\x1b\x69\x55\x70\x01")
    data = printer.read()
    result = interpret_response(data)
    if result['status_type'] != 'Settings report':
        raise ValueError
    logger.info(f"Auto power on: {'Enabled' if result['setting'] else 'Disabled'}")

    if write:
        # change auto power off settings
        printer.write(b"\x1b\x69\x61\x01") # Switch to raster command mode
        # 0x1b 0x69 0x55 settings
        # 0x41 auto power off
        # 0x00 write
        # u8 multiples of 10 minutes, 0 means disabled
        command = b"\x1b\x69\x55\x41\x00" + auto_power_off.to_bytes(1)
        printer.write(command)
        
        # retrieve status to make sure no errors occured
        printer.write(b"\x1b\x69\x53")  # "ESC i S" Status information request
        data = printer.read()
        result = interpret_response(data)
        if result['status_type'] != 'Reply to status request':
            raise ValueError("Failed to modify settings")

    # read auto power off settings
    printer.write(b"\x1b\x69\x61\x01") # Switch to raster command mode
    # 0x1b 0x69 0x55 settings
    # 0x41 auto power off
    # 0x01 read
    printer.write(b"\x1b\x69\x55\x41\x01") 
    data = printer.read()
    result = interpret_response(data)
    if result['status_type'] != 'Settings report':
        raise ValueError
    logger.info(f"Auto power off delay: {result['setting']*10} minutes")

    return result
