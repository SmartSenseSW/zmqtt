#!/usr/bin/env python3

import time
import datetime
import signal
import sys
import serial
import getopt
import struct
import RPi.GPIO as GPIO

_serial_port_sbl = None

SBL_CHUNK_SIZE = 64

HANDSHAKE_RETRY_COUNT = 50

MT_FRAME_SOF = 0xFE

# SBL MT SYS (cmd0 in the frame)
SBL_CMD_SYS = 0x4D

# SBL COMMANDS
SBL_WRITE_REQ = 0x01
SBL_WRITE_REQ_LEN = 66
SBL_WRITE_RESP = 0x81
SBL_WRITE_RESP_LEN = 1
SBL_READ_REQ = 0x02
SBL_READ_REQ_LEN = 2
SBL_READ_RESP = 0x82
SBL_READ_RESP_LEN = 67
SBL_ENABLE_REQ = 0x03
SBL_ENABLE_REQ_LEN = 0
SBL_ENABLE_RESP = 0x83
SBL_ENABLE_RESP_LEN = 1
SBL_HANDSHAKE_REQ = 0x04
SBL_HANDSHAKE_REQ_LEN = 0
SBL_HANDSHAKE_RESP = 0x84
SBL_HANDSHAKE_RESP_LEN = 1

SBL_STATUS_OK = 0
SBL_STATUS_FAIL = 1
SBL_STATUS_VALIDATION = 7


def timestamp():
    fmt = '%Y-%m-%d %H:%M:%S.%f'
    ts = time.time()
    return datetime.datetime.fromtimestamp(ts).strftime(fmt)


# logging helper
def LOG(severity, message):
    print(timestamp() + " " +
          str(sys.argv[0]) + " " +
          severity + " " +
          message)


def sbl_send_frame(frame):
    _serial_port_sbl.write(frame)
    print("serial send: " + ''.join('{:02x}'.format(x) for x in frame))


def mt_receive_message():
    # Start-Of-Packet byte
    rcv = _serial_port_sbl.read(1)

    if len(rcv) == 0:
        LOG('WRN', 'mt_receive_message: failed to receive SOF')
        return None
    
    sof = struct.unpack('B', rcv)[0]
    if sof != MT_FRAME_SOF:
        LOG('DBG', 'Looking For SOF ' + hex(sof))
        return None

    # next byte is data len
    rcv = _serial_port_sbl.read(1)
    if len(rcv) == 0:
        LOG('WRN', 'mt_receive_message: failed to receive payload len')
        return None
    datalen = struct.unpack('B', rcv)[0]
    checksum = datalen

    # then cmd0 byte
    rcv = _serial_port_sbl.read(1)
    if len(rcv) == 0:
        LOG('WRN', 'mt_receive_message: failed to receive cmd0')
        return None

    cmd0 = struct.unpack('B', rcv)[0]
    checksum = checksum ^ cmd0

    # and cmd1 byte
    rcv = _serial_port_sbl.read(1)
    if len(rcv) == 0:
        LOG('WRN', 'mt_receive_message: failed to receive cmd1')
        return None

    cmd1 = struct.unpack('B', rcv)[0]
    checksum = checksum ^ cmd1

    # now the message data
    data = []
    if datalen > 0:
        for i in range(datalen):
            rcv = _serial_port_sbl.read(1)
            if len(rcv) == 0:
                LOG('WRN', 'mt_receive_message: failed to receive payload (' +
                    str(i) + ' of ' + str(datalen) + ')')
                return None

            c = struct.unpack('B', rcv)[0]
            data.append(c)
            checksum = checksum ^ c

    # at the end Frame-Check-Sequence
    rcv = _serial_port_sbl.read(1)
    if len(rcv) == 0:
        LOG('WRN', 'mt_receive_message: failed to receive FCS')
        return None

    fcs = struct.unpack('B', rcv)[0]
    checksum = checksum ^ fcs

    LOG('DBG', 'mt_receive_message: ' +
        hex(sof) + ' ' +
        hex(datalen) + ' ' +
        hex(cmd0) + ' ' +
        hex(cmd1) + ' ' +
        ''.join('{:02x}'.format(x) for x in data) + ' ' +
        hex(fcs) + ' -> checksum=' +
        hex(checksum))

    # verify message
    if checksum != 0:
        LOG('WRN', 'checksum failed: ' + hex(checksum))
        return None

    return (cmd0, cmd1, data)


def mt_send_message(cmd0, cmd1, payload):
    # message is a tuple of cmd0, cmd1 and cmd data (list)
    frame = bytearray()
    checksum = 0

    # start of frame
    frame.append(MT_FRAME_SOF)

    # cmd data len
    datalen = len(payload)
    frame.append(datalen)
    checksum = datalen

    # cmd0
    frame.append(cmd0)
    checksum = checksum ^ cmd0

    # cmd1
    frame.append(cmd1)
    checksum = checksum ^ cmd1

    # data
    if datalen > 0:
        for byte in payload:
            frame.append(byte)
            checksum = checksum ^ byte

    # checksum
    frame.append(checksum)

    sbl_send_frame(frame)

    return
    
def reset_zigbee():
    GPIO.output(13, 0)
    time.sleep(1)
    GPIO.output(13, 1)
    time.sleep(1)
    return


def sbl_force_boot():
    # send boot force command to zigbee
    cmd = bytes([0x10])
    sbl_send_frame(cmd)
    return


def sbl_force_run():
    # send run force command to zigbee
    cmd = bytes([0xEF])
    sbl_send_frame(cmd)
    return


def sbl_cmd_handshake():
    respOk = False
    # assemble command
    cmd0 = SBL_CMD_SYS
    cmd1 = SBL_HANDSHAKE_REQ
    payload = bytearray()
    mt_send_message(cmd0, cmd1, payload)

    # get the response
    resp = mt_receive_message()
    if resp:
        cmd0 = resp[0]
        cmd1 = resp[1]
        payload = resp[2]
        datalen = len(payload)
        if cmd0 != SBL_CMD_SYS:
            LOG('DBG', 'sbl_cmd_handshake: invalid cmd0 (' + hex(cmd0) + ')')
        elif cmd1 != SBL_HANDSHAKE_RESP:
            LOG('DBG', 'sbl_cmd_handshake: invalid cmd1 (' + hex(cmd1) + ')')
        elif datalen != SBL_HANDSHAKE_RESP_LEN:
            LOG('DBG', 'sbl_cmd_handshake: invalid response size (' + hex(datalen) + ')')
        elif payload[0] != SBL_STATUS_OK:
            LOG('DBG', 'sbl_cmd_handshake: invalid response status (' + hex(payload[0]) + ')')
        else:
            # handshake ok
            respOk = True

    return respOk


def sbl_cmd_write(address, data):
    respOk = False
    # assemble command
    cmd0 = SBL_CMD_SYS
    cmd1 = SBL_WRITE_REQ
    payload = bytearray()

    # first the address
    payload.append(int(address) & 0x00FF)
    payload.append((int(address) & 0xFF00) >> 8)

    # then the data
    for x in data:
        payload.append(x)

    # fill the rest with 0xFF
    for i in range(len(data), SBL_CHUNK_SIZE):
        payload.append(0xFF)

    mt_send_message(cmd0, cmd1, payload)

    # get the response
    resp = mt_receive_message()
    if resp:
        cmd0 = resp[0]
        cmd1 = resp[1]
        payload = resp[2]
        datalen = len(payload)
        if cmd0 != SBL_CMD_SYS:
            LOG('DBG', 'sbl_cmd_write: invalid cmd0 (' + hex(cmd0) + ')')
        elif cmd1 != SBL_WRITE_RESP:
            LOG('DBG', 'sbl_cmd_write: invalid cmd1 (' + hex(cmd1) + ')')
        elif datalen != SBL_WRITE_RESP_LEN:
            LOG('DBG', 'sbl_cmd_write: invalid response size (' + hex(datalen) + ')')
        elif payload[0] != SBL_STATUS_OK:
            LOG('DBG', 'sbl_cmd_write: wrong response status (' + hex(payload[0]) + ')')
        else:
            # response ok
            respOk = True

    return respOk


def sbl_cmd_read(address, size):
    # assemble command
    cmd0 = SBL_CMD_SYS
    cmd1 = SBL_READ_REQ
    payload = bytearray()

    # the address
    payload.append(int(address) & 0x00FF)
    payload.append((int(address) & 0xFF00) >> 8)

    mt_send_message(cmd0, cmd1, payload)

    # get the response
    data = []
    resp = mt_receive_message()
    if resp:
        cmd0 = resp[0]
        cmd1 = resp[1]
        payload = resp[2]
        datalen = len(payload)
        if cmd0 != SBL_CMD_SYS:
            LOG('DBG', 'sbl_cmd_read: invalid cmd0 (' + hex(cmd0) + ')')
        elif cmd1 != SBL_READ_RESP:
            LOG('DBG', 'sbl_cmd_read: invalid cmd1 (' + hex(cmd1) + ')')
        elif datalen != SBL_READ_RESP_LEN:
            LOG('DBG', 'sbl_cmd_read: invalid response size (' + hex(datalen) + ')')
        elif payload[0] != SBL_STATUS_OK:
            LOG('DBG', 'sbl_cmd_read: wrong response status (' + hex(payload[0]) + ')')
        else:
            # response ok
            for i in range(3, 3 + size):
                data.append(payload[i])

    return data


def sbl_cmd_enable():
    respOk = False
    # assemble command
    cmd0 = SBL_CMD_SYS
    cmd1 = SBL_ENABLE_REQ
    payload = bytearray()
    mt_send_message(cmd0, cmd1, payload)

    # get the response
    resp = mt_receive_message()
    if resp:
        cmd0 = resp[0]
        cmd1 = resp[1]
        payload = resp[2]
        datalen = len(payload)
        if cmd0 != SBL_CMD_SYS:
            LOG('DBG', 'sbl_cmd_enable: invalid cmd0 (' + hex(cmd0) + ')')
        elif cmd1 != SBL_ENABLE_RESP:
            LOG('DBG', 'sbl_cmd_enable: invalid cmd1 (' + hex(cmd1) + ')')
        elif datalen != SBL_ENABLE_RESP_LEN:
            LOG('DBG', 'sbl_cmd_enable: invalid response size (' + hex(datalen) + ')')
        elif payload[0] != SBL_STATUS_OK:
            LOG('DBG', 'sbl_cmd_enable: invalid response status (' + hex(payload[0]) + ')')
        else:
            # handshake ok
            respOk = True

    return respOk


def sbl_read_simple_code_file(filename):
    fin = open(filename, 'rb')
    data = bytearray(fin.read())
    fin.close()
    return data


def sbl_write_simple_code_file(filename, data):
    fout = open(filename, "wb")
    fout.write(data)
    fout.close()
    return


def sbl_read_flash(datasize):
    address = 0
    data = bytearray()

    for start in range(0, datasize, SBL_CHUNK_SIZE):
        size = (
                SBL_CHUNK_SIZE if (datasize - start > SBL_CHUNK_SIZE)
                else (datasize - start))
        chunk = sbl_cmd_read(address, size)

        address = address + (size/4)
        data.extend(chunk)

    return data


def sbl_write_flash(progdata):

    datasize = len(progdata)

    # init address counter
    address = 0

    # while there is program data:
    for start in range(0, datasize, SBL_CHUNK_SIZE):
        end = (
                (start + SBL_CHUNK_SIZE) if
                (start + SBL_CHUNK_SIZE <= datasize) else
                datasize
              )
        chunk = progdata[start:end]

        # write 64 bytes (16 words)
        sbl_cmd_write(address, chunk)
        # increment address counter by a number of words written
        address = address + (len(chunk)/4)

    return


def sbl_vefify_flash(progdata, readdata):

    # check the sizes first
    if len(progdata) != len(readdata):
        return False

    for x, y in zip(progdata, readdata):
        if x != y:
            return False

    return True


def sbl_program_device(filename, forceOn):
    # read program data from file
    progdata = sbl_read_simple_code_file(filename)
    datasize = len(progdata)

    sbl_write_flash(progdata)

    if forceOn:
        # skip vefirication
        sbl_cmd_enable()
    else:
        # read flash
        readdata = sbl_read_flash(datasize)

        sbl_write_simple_code_file(filename + ".dbg", readdata)

        # compare flash content with program data
        if sbl_vefify_flash(progdata, readdata):
            # enable app if verification passed
            sbl_cmd_enable()
        else:
            print("error: flash verification failed")

    return


def sbl_read_device(filename, memsize):
    data = bytearray()

    # application memory is from address 0x2000
    size = (memsize * 1024) - 0x2000

    data = sbl_read_flash(size)

    sbl_write_simple_code_file(filename, data)

    return


# SIGINT signal handler: cleanup GPIO and exit
def sigint_handler(signal, frame):
    print('Ctrl-c pressed. Exit program.')
    GPIO.cleanup()
    sys.exit(0)


def usage():
    print("Zigbee coordinator serial bootloader ")
    print("Usage is:" + sys.argv[0] + " [options]")
    print("Options are:")
    print("--<t>ty tty device (/dev/ttyAMA0)")
    print("--<b>aud baudrate (115200)")
    print("--<i>mage file in simple code format")
    print("--<m>emory device flash memory size in kB (mandatory for read)")
    print("--<p>rogram device flash memory from the image file")
    print("--<r>ead device flash memory to the image file")
    print("--<f>orce skip verification when programming (optinal)")
    print("--<h>elp this message")


def main(argv):

    global _serial_port_sbl

    # default parameters
    uart_port = "/dev/ttyAMA0"
    uart_baudrate = 115200
    image_filename = ""
    programOn = False
    readOn = False
    forceOn = False
    memsize = 0

    try:
        opts, args = getopt.getopt(
                argv, "ht:b:n:i:m:prf",
                [
                    "help", "tty=", "baud=", "image=",
                    "memory", "program", "read", "force"
                ])
    except getopt.GetoptError:
        usage()
        sys.exit(2)
    for opt, arg in opts:
        if opt in ('-h', "--help"):
            usage()
            sys.exit()
        elif opt in ("-t", "--tty"):
            uart_port = arg
        elif opt in ("-b", "--baud"):
            uart_baudrate = arg
        elif opt in ("-i", "--image"):
            image_filename = arg
        elif opt in ("-m", "memory"):
            memsize = int(arg)
        elif opt in ("-p", "--program"):
            programOn = True
        elif opt in ("-r", "--read"):
            readOn = True
        elif opt in ("-f", "--force"):
            forceOn = True

    if image_filename == "":
        print("error: no image file specified")
        usage()
        return

    if programOn and readOn:
        print("error: multiple operations selected")
        usage()
        return

    if (not programOn) and (not readOn):
        print("error: no operation selected")
        usage()
        return

    if readOn and (memsize == 0):
        print("error: memory size must be specified for read operation")
        usage()
        return

    # install SIGINT signal handler
    signal.signal(signal.SIGINT, sigint_handler)

    LOG('DBG', 'open serial port')
    _serial_port_sbl = serial.Serial(uart_port, 
                                     baudrate=uart_baudrate,
                                     timeout=10)
    print("### configure gpio")
    GPIO.setmode(GPIO.BOARD)
    # GPIO.setup(3, GPIO.OUT)
    # GPIO.output(3,1)
    # GPIO.setup(5, GPIO.OUT)
    # GPIO.setup(5,1)
    GPIO.setup(13, GPIO.OUT)

    print("### reset zigbee")
    reset_zigbee()

    print("### force boot")
    sbl_force_boot()

    handshakeOK = False
    i = 0
    while ((not handshakeOK) and (i < HANDSHAKE_RETRY_COUNT)):
        i += 1
        sbl_force_boot()
        handshakeOK = sbl_cmd_handshake()
        LOG('DBG', 'waiting for handshake response')
        time.sleep(1)

    if ((not handshakeOK) and (i == HANDSHAKE_RETRY_COUNT)):
        LOG('ERR', 'failed to receive the handshake response')
        return

    if programOn:
        sbl_program_device(image_filename, forceOn)
        LOG('DBG', 'done programming flash')
    elif readOn:
        sbl_read_device(image_filename, memsize)
        LOG('DBG', 'done reading flash')

    return


if __name__ == "__main__":
    main(sys.argv[1:])
