#!/usr/bin/python3

import time
import signal
import sys
import serial
import paho.mqtt.client as mqtt
import re
import getopt
import os
import struct

# insert env variable for pi 1 before importing GPIO
try:
    with open('/proc/device-tree/system/linux,revision', 'rb') as f:
        rev = struct.unpack('>I', f.read(4))[0]
        if not (rev >> 23 & 0x1):
            os.environ['RPI_LGPIO_REVISION'] = '900030'
except:
    sys.exit(1)

import RPi.GPIO as GPIO
from threading import Timer
import sbl
#import otaserv
import subprocess
import string

_transitionTime = ":0"

zmqtt_version = "v3.0.0"

#_gw_rev = "GWMB"
_gw_rev = "GWMC"
_gwmb_led1_addr = 3
_gwmb_led2_addr = 5

_gwmc_led1_addr = 32
_gwmc_led2_addr = 33
_gwmc_led3_addr = 35
_gwmc_led4_addr = 3
_gwmc_led5_addr = 7

_gwmb_led1_timer = None
_gwmb_led2_timer = None

_gwmc_led1_timer = None
_gwmc_led2_timer = None
_gwmc_led3_timer = None
_gwmc_led4_timer = None
_gwmc_led5_timer = None

_ping_timer = None
_ping_timer_timeout = None
_ping_timer_timeout_flag = 0
PING_TIMER_TIMEOUT = 300

_uart_port = None
_serial_port = None
_mqttc = None
_gwid = ""
_gw_serial_sent = False
_gw_revision_sent = False
_just_started = 1
_coord_nid = ""
_seq_num = 0
_image_dir = "./ota_images"
_gw_image_file = "GWMC.bin"
_gw_version_file = "gw_version.txt"

_coord_mac = ""
_coord_mac_sent = False
_coord_nwk = ""
_coord_nwk_sent = False

_nid_mac_table = {}
_nid_nwk_table = {}

_ota_allowed = 0
SBL_FORCE = "v.0.0.0"

SOF = 0xFE
APP_MSG_CMD0 = 0x29
APP_MSG_CMD1 = 0x00
APP_ENDPOINT = 0x08
OTA_ENDPOINT = 20

SYSLOG_DBG = 0
SYSLOG_INF = 1
SYSLOG_WRN = 2
SYSLOG_ERR = 3

SYSLOG_SEVERITY_MAPPING = {SYSLOG_DBG:'DBG', SYSLOG_INF:'INFO', SYSLOG_WRN:'WRN', SYSLOG_ERR:'ERR'}
SYSLOG_SEVERITY = SYSLOG_INF



# SIGINT signal handler: cleanup GPIO and exit
def sigint_handler(signal, frame):
    LOG(SYSLOG_DBG,"Ctrl-c pressed. Exit program.")
    sys.stdout.flush()
    _serial_port.flushInput()
    _serial_port.flushOutput()
    GPIO.cleanup()
    led_exit()
    sys.exit(0)


# logging helper
def LOG(severity, message):
    global SYSLOG_SEVERITY
    if severity >= SYSLOG_SEVERITY:
        print(repr(str(sys.argv[0]) + " " + SYSLOG_SEVERITY_MAPPING[severity] + " " + message))
        sys.stdout.flush()


def get_gwid():
    # Base gateway id (gwid) on gateway MAC address.
    # API states that gwid is 8-byte, but gw has 6-byte MAC address.
    # Either we change the API spec or add two random bytes to generate gw id.

    mac = open('/sys/class/net/eth0/address').read()

    match = re.search("^([0-9A-Fa-f]{1,2}):([0-9A-Fa-f]{1,2}):([0-9A-Fa-f]{1,2}):([0-9A-Fa-f]{1,2}):([0-9A-Fa-f]{1,2}):([0-9A-Fa-f]{1,2})$", mac)
    if match:
        gwid = match.group(1).zfill(2) + \
               match.group(2).zfill(2) + \
               match.group(3).zfill(2) + \
               match.group(4).zfill(2) + \
               match.group(5).zfill(2) + \
               match.group(6).zfill(2)
    else:
        gwid = "None"

    return gwid

def add_nid_to_mac_table(nid, mac):
    _nid_mac_table[nid] = mac

def get_nid_mac(nid):
    return _nid_mac_table.get(nid)

def add_nid_to_nwk_table(nid, nwk):
    _nid_nwk_table[nid] = nwk

def get_nid_nwk(nid):
    return _nid_nwk_table.get(nid)

# MQTT callbacks
def on_connect(client, userdata, flags, rc):
    LOG(SYSLOG_INF, "MQ connected with result code:" + str(rc))
    mqtt_subscribe(client)
    #gw_send_serial()
    #gw_send_revision()

def on_message(client, userdata, msg):
    LOG(SYSLOG_DBG, "received mqtt message: <" + msg.topic + "> <" + msg.payload.decode('utf-8') + ">" )
    process_mqtt_message(msg)


def on_publish(client, userdata, mid):
    #LOG(SYSLOG_DBG, "MQ PUBLISHED: " + str(mid))
    return


def mqtt_subscribe(client):
    client.subscribe("smarthome/node/+/hw/zigbee/service")
    client.subscribe("smarthome/node/+/hw/led/+")
    client.subscribe("smarthome/node/+/sensor/+/+/sleep")
    client.subscribe("smarthome/node/+/sensor/+/+/reset")
    client.subscribe("smarthome/node/+/sensor/+/+/arm")
    client.subscribe("smarthome/node/+/sensor/+/+/switch/status")
    client.subscribe("smarthome/node/+/sensor/+/+/keepalive")
    client.subscribe("smarthome/node/+/sensor/+/+/set/+")
    client.subscribe("smarthome/node/+/sensor/+/+/query")
    client.subscribe("smarthome/gateway/+/hw/led/+")
    client.subscribe("smarthome/gateway/+/hw/reset/+")
    client.subscribe("smarthome/gateway/+/hw/ping/+")
    client.subscribe("smarthome/gateway/+/hw/sbl/+")
    client.subscribe("smarthome/gateway/+/hw/ota/+")
    client.subscribe("smarthome/gateway/+/hw/swdl/+")
    client.subscribe("smarthome/gateway/+/hw/lqi/+")
    client.subscribe("smarthome/platform/diagnostic/zdo/+")
    client.subscribe("smarthome/platform/diagnostic/loglevel/zmqtt")


def mqtt_msg_publish(topic, payload):
    """Sends mqtt message to the broker."""
    mqtt_msg_publish_x(topic, payload, 1, 1)

def mqtt_msg_publish_x(topic, payload, qos, retain):
    global _mqttc
    """Sends mqtt message to the broker."""
    LOG(SYSLOG_DBG, "mqtt_msg_publish: " + topic + "<" + payload + ">; qos=" + str(qos) + " retain=" + str(retain))
    spayload = "".join(filter(lambda x: x in string.printable, str(payload)))
    _mqttc.publish(str(topic), str(spayload), qos, retain)

def a2s(arr):
    """ Array of integer byte values --> binary string """
    return bytes(arr)

def ser_msg_send(message):
    """Sends a message to UART."""
    global _serial_port
    global _seq_num
    global _uart_port
    led_on("1")
#    _serial_port.write(message + "\n\r")

    message_with_mt = append_crc ('%0.2x'% _seq_num + "/" + message )

    try:
        if _serial_port.isOpen():
            _serial_port.write(message_with_mt)
    except serial.SerialException as e:
        LOG(SYSLOG_WRN, "Serial exception: " + str(e))
    except TypeError as e:
        LOG(SYSLOG_WRN, "UART Disconnected: " + str(e))
        _serial_port.close()
        _serial_port = serial.Serial(_uart_port, baudrate=uart_baudrate, timeout=15)

    LOG(SYSLOG_DBG, "send serial message: " + message_with_mt[5:-2].decode('ascii'))
    led_off("1")

    _seq_num += 1
    if (_seq_num == 256):
        _seq_num = 0

    time.sleep(0.5)

def append_crc(message):
    """calculate crc and append it too message"""

    checksum = 0
    message_bytes = message.encode('ascii')

    temp = a2s( [len(message_bytes) + 1, APP_MSG_CMD0, APP_MSG_CMD1, APP_ENDPOINT] ) + message_bytes
    for b in temp:
        checksum ^= b
    message_full = a2s( [SOF] ) + temp + a2s( [checksum] ) + b"\r"
    #message = "M/" + '%0.2x'% checksum + "/" + message
    return message_full

def sbl_handler (current_version):
    global _serial_port
    global _mqttc
    global _uart_port
    global _image_dir
    global _gw_version_file

    if _serial_port.isOpen():
        _serial_port.close()
    #_mqttc.disconnect()

    if os.path.isfile(_image_dir + '/' + _gw_version_file):
        fin = open(_image_dir + '/' + _gw_version_file, 'r')
        new_version = fin.readline().rstrip()
        fin.close()
        # version should be in format v<A>.<B>.<C>, where A, B and C are decimal numbers
        if re.match(r'v\d+\.?\d+\.?\d+', new_version):
            if new_version > current_version:
                LOG(SYSLOG_INF, "Newer version of GWMC.bin is found on system: " + str(new_version) + "; current version: " + str(current_version))
                if os.path.isfile(_image_dir + '/' + _gw_image_file):
                    try:
                        retcode = subprocess.call("sudo ./sbl.py -i " + _image_dir + '/' + _gw_image_file + " -t " + _uart_port + " -pf", shell=True)
                        if retcode < 0:
                            LOG(SYSLOG_WRN, "Subprocess sbl.py was terminated by signal : " + str(retcode))
                        else:
                            LOG(SYSLOG_INF, "Subprocess sbl.py was returned : " + str(retcode))
                    except OSError as e:
                        LOG(SYSLOG_WRN, "Execution of sbl.py failed: " + str(e))
                else:
                    LOG(SYSLOG_ERR, "GW image file is missing!")
                if(_serial_port.isOpen() != True):
                    _serial_port = serial.Serial(_uart_port, baudrate=115200, timeout=15)
                #_mqttc.reconnect()
                reset_zigbee("APP")
                time.sleep(0.5)
                init_msg_hendler_serial()
        else:
            LOG(SYSLOG_WRN, _gw_version_file + "contains invalid version: " + str(new_version))

    if(_serial_port.isOpen() != True):
        _serial_port = serial.Serial(_uart_port, baudrate=115200, timeout=15)
    #_mqttc.reconnect()

    return


def convert_mac_to_nid(mac):
    match = re.search("^([0-9A-Fa-f]{1,2}):([0-9A-Fa-f]{1,2}):([0-9A-Fa-f]{1,2}):([0-9A-Fa-f]{1,2}):([0-9A-Fa-f]{1,2}):([0-9A-Fa-f]{1,2}):([0-9A-Fa-f]{1,2}):([0-9A-Fa-f]{1,2})$", mac)

    if match:
        nid = match.group(1).zfill(2) + \
              match.group(2).zfill(2) + \
              match.group(3).zfill(2) + \
              match.group(4).zfill(2) + \
              match.group(5).zfill(2) + \
              match.group(6).zfill(2) + \
              match.group(7).zfill(2) + \
              match.group(8).zfill(2)
    else:
        nid = None

    return nid

def convert_nid_to_mac(nid):
    match = re.match("^([0-9A-Fa-f]){16}$", nid)
    if match is None:
        mac = None
    else:
        mac = nid[0].lstrip("0") + nid[1] + ":" + \
              nid[2].lstrip("0") + nid[3] + ":" + \
              nid[4].lstrip("0") + nid[5] + ":" + \
              nid[6].lstrip("0") + nid[7] + ":" + \
              nid[8].lstrip("0") + nid[9] + ":" + \
              nid[10].lstrip("0") + nid[11] + ":" + \
              nid[12].lstrip("0") + nid[13] + ":" + \
              nid[14].lstrip("0") + nid[15]

    return mac


def process_serial_message(message_bytes,client):
    #strip the line
    message = message_bytes.rstrip().decode('ascii')
    if message == '':
        # nothing to do
        return

    LOG(SYSLOG_DBG, "received serial message: <" + message + ">")

    match = re.search("([^/]+)/([^/]+)/([^/]+)/([^/]+)/([^/]+)", message)
    if match is None:
        LOG(SYSLOG_WRN, "invalid serial message: " + message)
        return

    node_id = match.group(1)
    mac= node_id
    msg_group = match.group(2)
    sensor_id = match.group(3)
    msg_type = match.group(4)
    msg_payload = match.group(5)
    '''
    print("NODE ID: " + node_id)
    print("GROUP: " + msg_group)
    print("SENSOR ID: " + sensor_id)
    print("TYPE: " + msg_type)
    print("PAYLOAD: " + msg_payload)
    '''

    node_id = convert_mac_to_nid(node_id)
    if node_id is None:
        LOG(SYSLOG_WRN, "process_serial_message: Invalid node id <" + mac + "> " + str(len(mac)))
        return

    if msg_group == "hw" or msg_group == "gw":
        # common topics
        ser_msg_handler_hw(msg_group, node_id, sensor_id, msg_type, msg_payload)

    if msg_group == "bat":
        # common topics
        ser_msg_handler_battery(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "p":
        ser_msg_handler_power(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "t":
        ser_msg_handler_temperature(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "h":
        ser_msg_handler_humidity(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "m":
        ser_msg_handler_motion(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "f":
        ser_msg_handler_fall(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "co2":
        ser_msg_handler_co2(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "voc":
        ser_msg_handler_voc(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "pm2_5":
        ser_msg_handler_pm2_5(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "pm10":
        ser_msg_handler_pm10(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "lux":
        ser_msg_handler_illuminance(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "pr":
        ser_msg_handler_pressure(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "blb":
        ser_msg_handler_bulb(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "cblb":
        ser_msg_handler_colorbulb(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "w":
        ser_msg_handler_water(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "d":
        ser_msg_handler_door(node_id, sensor_id, msg_type, msg_payload)

    elif msg_group == "sm":
        ser_msg_handler_smoke(node_id, sensor_id, msg_type, msg_payload)
    return


def ser_msg_handler_hw(msg_group, nodeid, sensorid, msgtype, payload):
    if (msgtype == "MAC"):
        ser_msg_handler_general_mac(msg_group, nodeid, sensorid, payload)
    elif (msgtype == "NWK"):
        ser_msg_handler_general_nwk(msg_group, nodeid, sensorid, payload)
    elif (msgtype == "lqi"):
        ser_msg_handler_general_lqi(msg_group, nodeid, sensorid, payload)
    elif (msgtype == "ieee"):
        ser_msg_handler_general_ieee(msg_group, nodeid, sensorid, payload)
    elif (msgtype == "role"):
        ser_msg_handler_general_role(msg_group, nodeid, sensorid, payload)
    elif (msgtype == "rev"):
        ser_msg_handler_general_revision(msg_group, nodeid, sensorid, payload)
    elif (msgtype == "ser"):
        ser_msg_handler_general_serial(msg_group, nodeid, sensorid, payload)
    elif (msgtype == "bt"):
        ser_msg_handler_general_button(msg_group, nodeid, sensorid, payload)
    elif (msgtype == "sw"):
        ser_msg_handler_general_version(msg_group, nodeid, sensorid, payload)
    elif (msgtype == "crc"):
        ser_msg_handler_general_crc(msg_group, nodeid, sensorid, payload)
    elif (msgtype == "chn"):
        ser_msg_handler_general_channel(msg_group, nodeid, sensorid, payload)
    elif (msgtype == "ping"):
        ser_msg_handler_general_ping(msg_group, nodeid, sensorid, payload)
    elif (msgtype == "nv_mem"):
        ser_msg_handler_general_nv_mem(msg_group, nodeid, sensorid, payload)
    elif (msgtype == "heap"):
        ser_msg_handler_general_heap(msg_group, nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown general message: " + msgtype)
        return


def ser_msg_handler_general_mac(msg_group, nodeid, sensorid, payload):
    global _coord_nid

    mac = convert_mac_to_nid(payload)
    if mac is None:
        LOG(SYSLOG_WRN, "ser_msg_handler_general_mac: failed to convert mac address, use received value")
        mac = payload
    if msg_group == "hw":
        topic = "smarthome/node/" + nodeid + "/hw/zigbee/MAC"

    elif msg_group == "gw":
        topic = "smarthome/gateway/" + nodeid + "/hw/zigbee/MAC"

    mqtt_msg_publish(topic, mac)

    # add node to the mac table and send the coordinator node  mac address
    add_nid_to_mac_table(nodeid, mac)
    if nodeid == _coord_nid:
        gw_coord_mac_send()


def ser_msg_handler_general_nwk(msg_group, nodeid, sensorid, payload):
    global _coord_nid

    nwk = payload

    if msg_group == "hw":
        topic = "smarthome/node/" + nodeid + "/hw/zigbee/NWK"

    elif msg_group == "gw":
        topic = "smarthome/gateway/" + nodeid + "/hw/zigbee/NWK"

    mqtt_msg_publish(topic, nwk)

    add_nid_to_nwk_table(nodeid, nwk)
    if nodeid == _coord_nid:
        gw_coord_nwk_send()


#def ser_msg_handler_general_lqi(msg_group, nodeid, sensorid, payload): 
#    topic = "smarthome/gateway/" + nodeid + "/hw/zigbee/LQI"
#    unit = "dBm"
#    mqtt_msg_publish(topic, payload)


def ser_msg_handler_general_ieee(msg_group, nodeid, sensorid, payload): 
    topic = "smarthome/platform/diagnostic/zdo/ieee_resp"
    mqtt_msg_publish_x(topic, payload, 2, 0)


def ser_msg_handler_general_lqi(msg_group, nodeid, sensorid, payload): 
    topic = "smarthome/platform/diagnostic/zdo/lqi_resp"
    mqtt_msg_publish_x(topic, payload, 2, 0)


def ser_msg_handler_general_role(msg_group, nodeid, sensorid, payload):
    global _coord_nid

    if payload == "c":
        value = "coordinator"
        _coord_nid = nodeid
        # try to send coord nwk and mac, maybe we acquired all data
        # removed (Simon, wrong mac)
        #gw_coord_mac_send()
        #gw_coord_nwk_send()
    elif payload == "r":
        value = "router"
    elif payload == "e":
        value = "end device"
    else:
        LOG(SYSLOG_ERR, "ser_msg_handler_general_role: invalid payload <" + payload.decode('utf-8') + ">")
        return

    if msg_group == "hw":
        topic = "smarthome/node/" + nodeid + "/hw/zigbee/role"

    elif msg_group == "gw":
        topic = "smarthome/gateway/" + nodeid + "/hw/zigbee/role"

    mqtt_msg_publish(topic, value)


def ser_msg_handler_general_revision(msg_group, nodeid, sensorid, payload):
    if msg_group == "hw":
        topic = "smarthome/node/" + nodeid + "/hw/revision"
        mqtt_msg_publish(topic, payload)
        if "RGB" in payload:
            ser_msg_handler_colorbulb_status(nodeid, "0006", "1")
            ser_msg_handler_colorbulb_level(nodeid, "0006", "75")
            ser_msg_handler_colorbulb_hue(nodeid, "0006", "48")
            ser_msg_handler_colorbulb_saturation(nodeid, "0006", "91")

    elif msg_group == "gw":
        topic = "smarthome/gateway/" + nodeid + "/hw/revision"
        mqtt_msg_publish(topic, payload)

        #send sw version and zmqtt version
        topic = "smarthome/gateway/" + nodeid + "/sw/version"
        #f = open ('/version', 'r')
        payload = "FREE"
        mqtt_msg_publish(topic, payload)

        topic = "smarthome/gateway/" + nodeid + "/sw/zmq_version"
        payload = zmqtt_version
        mqtt_msg_publish(topic, payload)


def ser_msg_handler_general_serial(msg_group, nodeid, sensorid, payload):
    payload = "".join(filter(lambda x: x in string.printable, payload))

    if msg_group == "hw":
        topic = "smarthome/node/" + nodeid + "/hw/serial"

    elif msg_group == "gw":
        topic = "smarthome/gateway/" + nodeid + "/hw/serial"
#        f = open('/serial','w')
#        f.write(payload)
#        f.close

    mqtt_msg_publish(topic, payload)


def ser_msg_handler_general_button(msg_group, nodeid, sensorid, payload):
    if msg_group == "hw":
        topic = "smarthome/node/" + nodeid + "/hw/button/" + sensorid

    elif msg_group == "gw":
        topic = "smarthome/gateway/" + nodeid + "/hw/button/" + sensorid

    mqtt_msg_publish(topic, payload)


def ser_msg_handler_general_version(msg_group, nodeid, sensorid, payload):
    global _just_started

    if msg_group == "hw":
        topic = "smarthome/node/" + nodeid + "/sw/version"

    elif msg_group == "gw":
        topic = "smarthome/gateway/" + nodeid + "/sw/rf_version"
        if (_just_started == 1):
            sbl_handler(payload)
            _just_started = 0
    mqtt_msg_handler_gateway_ota(0,0,"START")
    mqtt_msg_publish(topic, payload)


def ser_msg_handler_general_crc(msg_group, nodeid, sensorid, payload):

    topic = "smarthome/node/" + nodeid + "/sw/crc"
    mqtt_msg_publish(topic, payload)


def ser_msg_handler_general_channel(msg_group, nodeid, sensorid, payload):
    if msg_group == "gw":
        topic = "smarthome/gateway/" + nodeid + "/gw/channel"
        mqtt_msg_publish(topic, payload)


def ser_msg_handler_general_ping(msg_group, nodeid, sensorid, payload):
    global _ping_timer_timeout_flag

    if msg_group == "gw":
        topic = "smarthome/gateway/" + nodeid + "/gw/ping"
        _ping_timer_timeout_flag = 1
        mqtt_msg_publish(topic, payload)


def ser_msg_handler_general_nv_mem(msg_group, nodeid, sensorid, payload):
    mqtt_msg_publish("smarthome/platform/diagnostic/nv_mem", payload)


def ser_msg_handler_general_heap(msg_group, nodeid, sensorid, payload):
    mqtt_msg_publish("smarthome/platform/diagnostic/heap", payload)


def ser_msg_handler_battery(nodeid, sensorid, msgtype, payload):
    if (msgtype == "v"):
        ser_msg_handler_battery_voltage(nodeid, sensorid, payload)
    elif (msgtype == "est"):
        ser_msg_handler_battery_estimate(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_ERR, "ser_msg_handler_battery: unknown battery message <" + msgtype + ">")
        return


def ser_msg_handler_battery_voltage(nodeid, sensorid, payload):
    topic = "smarthome/node/" + nodeid + "/battery/voltage"
    voltage = str(float(payload)/100)
    mqtt_msg_publish(topic, voltage)
    # dok ne smislimo pametniji nacin radimo linearnu aproksimaciju 1-1,5V
    topic = "smarthome/node/" + nodeid + "/battery/estimate"
    estimate = int(((float(voltage) - 1.8) / 1.2)*100)
    if estimate > 100:
        estimate = 100
    elif estimate < 1:
        estimate = 1

    mqtt_msg_publish(topic, str(estimate))


def ser_msg_handler_battery_estimate(nodeid, sensorid, payload):
    topic = "smarthome/node/" + nodeid + "/battery/estimate"
    mqtt_msg_publish(topic, payload)

    topic = "smarthome/node/" + nodeid + "/battery/voltage"
    mqtt_msg_publish(topic, "3")


def ser_msg_handler_power(nodeid, sensorid, msgtype, payload):
    if (msgtype == "st"):
        ser_msg_handler_power_status(nodeid, sensorid, payload)

    elif (msgtype == "p"):
        ser_msg_handler_power_power(nodeid, sensorid, payload)

    elif (msgtype == "e"):
        ser_msg_handler_power_energy(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown power message: " + msgtype)
        return


def ser_msg_handler_power_status(nodeid, sensorid, payload):
    if payload == "0":
        value = "false"
    elif payload == "1":
        value = "true"
    else:
        LOG(SYSLOG_ERR, "ser_msg_handler_power_status: invalid payload <" + payload.decode('utf-8') + ">")
        return

    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    topic = "smarthome/node/" + nodeid + "/sensor/power/" + sensorid + "/value/status"
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic, value)


def ser_msg_handler_power_power(nodeid, sensorid, payload):
    topic = "smarthome/node/" + nodeid + "/sensor/power/" + sensorid + "/value/power"
    unit = "W"

    power = str(float(payload)/100)
    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic.replace("value", "unit"), unit)
    mqtt_msg_publish(topic, power)


def ser_msg_handler_power_energy(nodeid, sensorid, payload):
    topic = "smarthome/node/" + nodeid + "/sensor/power/" + sensorid + "/value/energy"
    unit = "kWh"
    #energy = str(float(payload) / 100000)
    energy = str("{0:.10f}".format(float(payload) / 100000))

    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic.replace("value", "unit"), unit)
    mqtt_msg_publish(topic, energy)



def ser_msg_handler_temperature(nodeid, sensorid, msgtype, payload):
    if msgtype == "act":
        ser_msg_handler_temperature_actual(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown temperature message: " + msgtype)
        return


def ser_msg_handler_temperature_actual(nodeid, sensorid, payload):
    topic = "smarthome/node/" + nodeid + "/sensor/temperature/" + sensorid + "/value/actual"
    unit = "oC"
    actual = str(float(payload) / 100)
    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic.replace("value/actual", "unit"), unit)
    mqtt_msg_publish(topic, actual)


def ser_msg_handler_humidity(nodeid, sensorid, msgtype, payload):
    if msgtype == "act":
        ser_msg_handler_humidity_value(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown humidity message: " + msgtype)


def ser_msg_handler_humidity_value(nodeid, sensorid, payload):
    topic = "smarthome/node/" + nodeid + "/sensor/humidity/" + sensorid + "/value/actual"
    unit = "%"
    actual = str(float(payload) / 100)
    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic.replace("value/actual", "unit"), unit)
    mqtt_msg_publish(topic, actual)


def ser_msg_handler_motion(nodeid, sensorid, msgtype, payload):
    if msgtype == "st":
        ser_msg_handler_motion_status(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown motion message: " + msgtype)
        return


def ser_msg_handler_motion_status(nodeid, sensorid, payload):
    if payload == "0":
        value = "IDLE"
#        value = "false"
    elif payload == "1":
#        value = "true"
        value = "ACTIVE"
    else:
        LOG(SYSLOG_ERR, "ser_msg_handler_motion_status: invalid payload <" + payload + ">")
        return

    topic = "smarthome/node/" + nodeid + "/sensor/motion/" + sensorid + "/value/status"
    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic, value)


def ser_msg_handler_fall(nodeid, sensorid, msgtype, payload):
    if msgtype == "st":
        ser_msg_handler_fall_status(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown fall message: " + msgtype)
        return


def ser_msg_handler_fall_status(nodeid, sensorid, payload):
    if payload == "0":
        value = "OK"
    elif payload == "1":
        value = "FALL"
    elif payload == "2":
        value = "PANIC"
    else:
        LOG(SYSLOG_ERR, "ser_msg_handler_fall_status: invalid payload <" + payload + ">")
        return

    topic = "smarthome/node/" + nodeid + "/sensor/fall/" + sensorid + "/value/status"
    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic, value)

def ser_msg_handler_co2(nodeid, sensorid, msgtype, payload):
    if msgtype == "act":
        ser_msg_handler_co2_actual(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown fall message: " + msgtype)
        return

def ser_msg_handler_co2_actual(nodeid, sensorid, payload):
    topic = "smarthome/node/" + nodeid + "/sensor/co2/" + sensorid + "/value/actual"
    unit = "ppm"
    actual = str(payload)
    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic.replace("value/actual", "unit"), unit)
    mqtt_msg_publish(topic, actual)



def ser_msg_handler_voc(nodeid, sensorid, msgtype, payload):
    if msgtype == "act":
        ser_msg_handler_voc_actual(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown voc message: " + msgtype)
        return

def ser_msg_handler_voc_actual(nodeid, sensorid, payload):
    topic = "smarthome/node/" + nodeid + "/sensor/voc/" + sensorid + "/value/actual"
    unit = "ppb"
    actual = str(payload)
    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic.replace("value/actual", "unit"), unit)
    mqtt_msg_publish(topic, actual)


def ser_msg_handler_pm2_5(nodeid, sensorid, msgtype, payload):
    if msgtype == "act":
        ser_msg_handler_pm2_5_actual(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown pm2.5 message: " + msgtype)
        return


def ser_msg_handler_pm2_5_actual(nodeid, sensorid, payload):
    topic = "smarthome/node/" + nodeid + "/sensor/pm2_5/" + sensorid + "/value/actual"
    unit = "ugm3"
    actual = str(payload)
    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic.replace("value/actual", "unit"), unit)
    mqtt_msg_publish(topic, actual)


def ser_msg_handler_pm10(nodeid, sensorid, msgtype, payload):
    if msgtype == "act":
        ser_msg_handler_pm10_actual(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown pm10 message: " + msgtype)
        return


def ser_msg_handler_pm10_actual(nodeid, sensorid, payload):
    topic = "smarthome/node/" + nodeid + "/sensor/pm10/" + sensorid + "/value/actual"
    unit = "ugm3"
    actual = str(payload)
    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic.replace("value/actual", "unit"), unit)
    mqtt_msg_publish(topic, actual)


def ser_msg_handler_illuminance(nodeid, sensorid, msgtype, payload):
    if msgtype == "act":
        ser_msg_handler_illuminance_actual(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown illuminance message: " + msgtype)
        return


def ser_msg_handler_illuminance_actual(nodeid, sensorid, payload):
    topic = "smarthome/node/" + nodeid + "/sensor/illuminance/" + sensorid + "/value/actual"
    unit = "lux"
    actual = str(payload)
    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic.replace("value/actual", "unit"), unit)
    mqtt_msg_publish(topic, actual)


def ser_msg_handler_pressure(nodeid, sensorid, msgtype, payload):
    if msgtype == "act":
        ser_msg_handler_pressure_actual(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown pressure message: " + msgtype)
        return


def ser_msg_handler_pressure_actual(nodeid, sensorid, payload):
    topic = "smarthome/node/" + nodeid + "/sensor/pressure/" + sensorid + "/value/actual"
    unit = "hPa"
    actual = str(float(payload) / 10)
    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic.replace("value/actual", "unit"), unit)
    mqtt_msg_publish(topic, actual)


def ser_msg_handler_bulb(nodeid, sensorid, msgtype, payload):
    if (msgtype == "st"):
        ser_msg_handler_bulb_status(nodeid, sensorid, payload)
    elif (msgtype == "lv"):
        ser_msg_handler_bulb_level(nodeid, sensorid, payload)  
    else:
        LOG(SYSLOG_WRN, "unknown bulb message: " + msgtype)
        return

def ser_msg_handler_bulb_status(nodeid, sensorid, payload):
    if payload == "0":
        value = "false"
    elif payload == "1":
        value = "true"
    else:
        LOG(SYSLOG_ERR, "ser_msg_handler_bulb_status: invalid payload <" + payload + ">")
        return

    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    topic = "smarthome/node/" + nodeid + "/sensor/bulb/" + sensorid + "/value/switch"
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic, value)

def ser_msg_handler_bulb_level(nodeid, sensorid, payload):
    if int(payload) >= 0 and int(payload) <= 100:
        timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
        topic = "smarthome/node/" + nodeid + "/sensor/bulb/" + sensorid + "/value/level"
        mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
        mqtt_msg_publish(topic, payload)
    else:
        LOG(SYSLOG_ERR, "ser_msg_handler_bulb_level: invalid payload <" + payload + ">")
        return

def ser_msg_handler_colorbulb(nodeid, sensorid, msgtype, payload):
    if (msgtype == "st"):
        ser_msg_handler_colorbulb_status(nodeid, sensorid, payload)
    elif (msgtype == "lv"):
        ser_msg_handler_colorbulb_level(nodeid, sensorid, payload)
    elif (msgtype == "hue"):
        ser_msg_handler_colorbulb_hue(nodeid, sensorid, payload)
    elif (msgtype == "sat"):
        ser_msg_handler_colorbulb_saturation(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown bulb message: " + msgtype)
        return

def ser_msg_handler_colorbulb_status(nodeid, sensorid, payload):
    if payload == "0":
        value = "false"
    elif payload == "1":
        value = "true"
    else:
        LOG(SYSLOG_ERR, "ser_msg_handler_colorbulb_status: invalid payload <" + payload + ">")
        return

    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    topic = "smarthome/node/" + nodeid + "/sensor/colorbulb/" + sensorid + "/value/switch"
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic, value)

def ser_msg_handler_colorbulb_level(nodeid, sensorid, payload):
    if int(payload) >= 0 and int(payload) <= 100:
        timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
        topic = "smarthome/node/" + nodeid + "/sensor/colorbulb/" + sensorid + "/value/level"
        mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
        mqtt_msg_publish(topic, payload)
    else:
        LOG(SYSLOG_ERR, "ser_msg_handler_colorbulb_level: invalid payload <" + payload + ">")
        return

def ser_msg_handler_colorbulb_hue(nodeid, sensorid, payload):
    if int(payload) >= 0 and int(payload) <= 360:
        timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
        topic = "smarthome/node/" + nodeid + "/sensor/colorbulb/" + sensorid + "/value/hue"
        mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
        mqtt_msg_publish(topic, payload)
    else:
        LOG(SYSLOG_ERR, "ser_msg_handler_colorbulb_hue: invalid payload <" + payload + ">")
        return

def ser_msg_handler_colorbulb_saturation(nodeid, sensorid, payload):
    if int(payload) >= 0 and int(payload) <= 100:
        timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
        topic = "smarthome/node/" + nodeid + "/sensor/colorbulb/" + sensorid + "/value/saturation"
        mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
        mqtt_msg_publish(topic, payload)
    else:
        LOG(SYSLOG_ERR, "ser_msg_handler_colorbulb_saturation: invalid payload <" + payload + ">")
        return

def ser_msg_handler_water(nodeid, sensorid, msgtype, payload):
    if (msgtype == "st"):
        ser_msg_handler_water_status(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown bulb message: " + msgtype)
        return

def ser_msg_handler_water_status(nodeid, sensorid, payload):

    if payload == "0":
      value = "false"
    elif payload == "1":
      value = "true"
    else:
        LOG(SYSLOG_ERR, "ser_msg_handler_water_status: invalid payload <" + payload + ">")
        return

    topic = "smarthome/node/" + nodeid + "/sensor/water/1031/value/status"
    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic, value)

def ser_msg_handler_door(nodeid, sensorid, msgtype, payload):
    if (msgtype == "st"):
        ser_msg_handler_door_status(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown bulb message: " + msgtype)
        return

def ser_msg_handler_door_status(nodeid, sensorid, payload):

    if payload == "0":
      value = "false"
    elif payload == "1":
      value = "true"
    else:
        LOG(SYSLOG_ERR, "ser_msg_handler_door_status: invalid payload <" + payload + ">")
        return

    topic = "smarthome/node/" + nodeid + "/sensor/door/1032/value/status"
    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic, value)

def ser_msg_handler_smoke(nodeid, sensorid, msgtype, payload):
    if (msgtype == "st"):
        ser_msg_handler_smoke_status(nodeid, sensorid, payload)
    else:
        LOG(SYSLOG_WRN, "unknown bulb message: " + msgtype)
        return

def ser_msg_handler_smoke_status(nodeid, sensorid, payload):

    if payload == "0":
      value = "false"
    elif payload == "1":
      value = "true"
    else:
        LOG(SYSLOG_ERR, "ser_msg_handler_smoke_status: invalid payload <" + payload + ">")
        return

    topic = "smarthome/node/" + nodeid + "/sensor/smoke/1033/value/status"
    timestamp = time.strftime("%d.%m.%Y %H:%M:%S")
    mqtt_msg_publish(topic.replace("value", "timestamp"), timestamp)
    mqtt_msg_publish(topic, value)


def process_mqtt_message(msg):
    # 1 node level messages
    # smarthome/node/[node_id]/hw/zigbee/service
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/hw/zigbee/service", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_hw_service(mac, msg.payload)
        return

    # smarthome/node/[node_id]/hw/led/[led_id]
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/hw/led/([^/]+)", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_hw_led(mac, match.group(2), msg.payload)
        return

    # 2 sensor level messages
    # 2-1 power messages 
    # smarthome/node/[node_id]/sensor/power/[sensor_id]/switch/status
    # smarthome/node/[node_id]/sensor/power/[sensor_id]/set/switch
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/power/([^/]+)/switch/status", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_pwr_switch(mac, match.group(2), msg.payload)
        return
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/power/([^/]+)/set/switch", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_pwr_switch(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/power/[sensor_id]/sleep
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/power/([^/]+)/sleep", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_pwr_sleep(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/power/[sensor_id]/reset/energy
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/power/([^/]+)/reset/energy", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_pwr_reset(mac, match.group(2), msg.payload)
        return

    # 2-2 temperature messages
    # smarthome/node/[node_id]/sensor/temperature/[sensor_id]/sleep
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/temperature/([^/]+)/sleep", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_temp_sleep(mac, match.group(2), msg.payload)
        return

    # 2-3 humidity messages
    # smarthome/node/[node_id]/sensor/humidity/[sensor_id]/sleep
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/humidity/([^/]+)/sleep", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_humidity_sleep(mac, match.group(2), msg.payload)
        return 

    # 2-4 motion messages
    # smarthome/node/[node_id]/sensor/motion/[sensor_id]/arm
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/motion/([^/]+)/arm", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_motion_arm(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/motion/[sensor_id]/sleep
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/motion/([^/]+)/sleep", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_motion_sleep(mac, match.group(2), msg.payload)
        return

    # 2-5 fall messages
    # smarthome/node/[node_id]/sensor/fall/[sensor_id]/arm
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/fall/([^/]+)/arm", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_fall_arm(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/fall/[sensor_id]/keepalive
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/fall/([^/]+)/keepalive", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_fall_keepalive(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/co2/[sensor_id]/sleep
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/co2/([^/]+)/sleep", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_co2_sleep(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/voc/[sensor_id]/sleep
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/voc/([^/]+)/sleep", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_voc_sleep(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/pm2_5/[sensor_id]/sleep
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/pm2_5/([^/]+)/sleep", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_pm2_5_sleep(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/pm10/[sensor_id]/sleep
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/pm10/([^/]+)/sleep", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_pm10_sleep(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/illuminance/[sensor_id]/sleep
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/illuminance/([^/]+)/sleep", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_illuminance_sleep(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/pressure/[sensor_id]/sleep
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/pressure/([^/]+)/sleep", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_pressure_sleep(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/bulb/[sensor_id]/set/switch
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/bulb/([^/]+)/set/switch", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_bulb_switch(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/bulb/[sensor_id]/set/level
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/bulb/([^/]+)/set/level", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_bulb_level(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/bulb/[sensor_id]/query
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/bulb/([^/]+)/query", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_bulb_query(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/colorbulb/[sensor_id]/set/switch
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/colorbulb/([^/]+)/set/switch", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_colorbulb_switch(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/colorbulb/[sensor_id]/set/level
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/colorbulb/([^/]+)/set/level", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_colorbulb_level(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/colorbulb/[sensor_id]/set/hue
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/colorbulb/([^/]+)/set/hue", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_colorbulb_hue(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/colorbulb/[sensor_id]/set/saturation
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/colorbulb/([^/]+)/set/saturation", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_colorbulb_saturation(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/colorbulb/[sensor_id]/set/hsv
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/colorbulb/([^/]+)/set/hsv", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_colorbulb_hsv(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/colorbulb/[sensor_id]/set/temperature
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/colorbulb/([^/]+)/set/temperature", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_colorbulb_temperature(mac, match.group(2), msg.payload)
        return

    # smarthome/node/[node_id]/sensor/colorbulb/[sensor_id]/query
    match = re.search("^smarthome/node/([0-9A-Fa-f]{16})/sensor/colorbulb/([^/]+)/query", msg.topic)
    if match:
        mac = convert_nid_to_mac(match.group(1))
        mqtt_msg_handler_sensor_colorbulb_query(mac, match.group(2), msg.payload)
        return

    # smarthome/gateway/[gw_id]/hw/led/[led_id]
    match = re.search("smarthome/gateway/([^/]+)/hw/led/([^/]+)", msg.topic)
    if match:
        mqtt_msg_handler_gateway_led(match.group(1), match.group(2), msg.payload)
        return

    # smarthome/gateway/[gw_id]/hw/reset/[reset_id]
    match = re.search("smarthome/gateway/([^/]+)/hw/reset/([^/]+)", msg.topic)
    if match:
        mqtt_msg_handler_gateway_reset(match.group(1), match.group(2), msg.payload)
        return

    # smarthome/gateway/[gw_id]/hw/ping/[ping_id]
    match = re.search("smarthome/gateway/([^/]+)/hw/ping/([^/]+)", msg.topic)
    if match:
        mqtt_msg_handler_gateway_ping(match.group(1), match.group(2), msg.payload)
        return

    match = re.search("smarthome/platform/diagnostic/zdo/([^/]+)", msg.topic)
    if match:
        mqtt_msg_handler_gateway_zdo(match.group(1), msg.payload)
        return

    match = re.search("smarthome/platform/diagnostic/loglevel/zmqtt", msg.topic)
    if match:
        mqtt_msg_handler_gateway_loglevel(msg.payload)
        return

    # smarthome/gateway/[gw_id]/hw/sbl/[sbl_id]
    match = re.search("smarthome/gateway/([^/]+)/hw/sbl/([^/]+)", msg.topic)
    if match:
        mqtt_msg_handler_gateway_sbl(match.group(1), match.group(2), msg.payload)
        return

    # smarthome/gateway/[gw_id]/hw/ota/[ota_id]
    match = re.search("smarthome/gateway/([^/]+)/hw/ota/([^/]+)", msg.topic)
    if match:
        mqtt_msg_handler_gateway_ota(match.group(1), match.group(2), msg.payload)
        return

    # smarthome/gateway/[gw_id]/hw/swdl/[swdl_id]
    match = re.search("smarthome/gateway/([^/]+)/hw/swdl/([^/]+)", msg.topic)
    if match:
        mqtt_msg_handler_gateway_swdl(match.group(1), match.group(2), msg.payload)
        return

    # smarthome/gateway/[gw_id]/hw/lqi/[lqi_id]
    match = re.search("smarthome/gateway/([^/]+)/hw/lqi/([^/]+)", msg.topic)
    if match:
        mqtt_msg_handler_gateway_lqi(match.group(1), match.group(2), msg.payload)
        return

    # error - unknown message
    LOG(SYSLOG_ERR, "Received unknown or invalid mqtt message <" + msg.topic + "> <" + msg.payload +">")
    return


def mqtt_msg_handler_hw_service(mac, payload):
    if payload == "OFF":
        value = 0
    elif payload == "ON":
        value = 1
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_hw_service: invalid payload <" + payload + ">")
        return

    message = str(mac) + "/hw/0/srv/" + str(value)
    ser_msg_send(message)


def mqtt_msg_handler_hw_led(mac, ledid, payload):
    if payload == "OFF":
        value = 0
    elif payload == "ON":
        value = 1
    elif payload == "BLINK":
        value = 2
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_hw_led: invalid payload <" + payload + ">")
        return

    message = str(mac) + "/hw/" + str(ledid) +"/led/" + str(value)
    ser_msg_send(message)


def mqtt_msg_handler_sensor_pwr_switch(mac, sensorid, payload):
    if payload == b"false":
        value = 0
    elif payload == b"true":
        value = 1
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_sensor_pwr_switch: invalid payload <" + payload.decode('utf-8') + ">")
        return

    message = str(mac) + "/p/" + str(sensorid) +"/sw/" + str(value)
    ser_msg_send(message)


def mqtt_msg_handler_sensor_pwr_sleep(mac, sensorid, payload):
    payload = str(int(payload) / 1000)
    message = str(mac) + "/p/" + str(sensorid) +"/sl/" + str(payload)
    ser_msg_send(message)


def mqtt_msg_handler_sensor_pwr_reset(mac, sensorid, payload):
    message = str(mac) + "/p/" + str(sensorid) +"/re/" + str(payload)
    ser_msg_send(message)


def mqtt_msg_handler_sensor_temp_sleep(mac, sensorid, payload):
    payload = str(int(payload) / 1000)
    message = str(mac) + "/t/" + str(sensorid) +"/sl/" + str(payload)
    ser_msg_send(message)


def mqtt_msg_handler_sensor_humidity_sleep(mac, sensorid, payload):
    payload = str(int(payload) / 1000)
    message = str(mac) + "/h/" + str(sensorid) +"/sl/" + str(payload)
    ser_msg_send(message)


def mqtt_msg_handler_sensor_motion_arm(mac, sensorid, payload):
    if payload == "DISARM":
        value = "0"
    elif payload == "ARM":
        value = "1"
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_sensor_motion_arm: invalid payload <" + payload + ">")
        return
    message = str(mac) + "/m/" + str(sensorid) +"/arm/" + value
    ser_msg_send(message)


def mqtt_msg_handler_sensor_motion_sleep(mac, sensorid, payload):
    payload = str(int(payload) / 1000)
    message = str(mac) + "/m/" + str(sensorid) +"/sl/" + str(payload)
    ser_msg_send(message)


def mqtt_msg_handler_sensor_fall_arm(mac, sensorid, payload):
    if payload == "DISARM":
        value = "0"
    elif payload == "ARM":
        value = "1"
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_sensor_fall_arm: invalid payload <" + payload + ">")
        return
    message = str(mac) + "/f/" + str(sensorid) +"/arm/" + value
    ser_msg_send(message)


def mqtt_msg_handler_sensor_fall_keepalive(mac, sensorid, payload):
    message = str(mac) + "/f/" + str(sensorid) +"/ka/" + str(payload)
    ser_msg_send(message)

def mqtt_msg_handler_sensor_co2_sleep(mac, sensorid, payload):
    payload = str(int(payload) / 1000)
    message = str(mac) + "/co2/" + str(sensorid) +"/sl/" + str(payload)
    ser_msg_send(message)

def mqtt_msg_handler_sensor_voc_sleep(mac, sensorid, payload):
    payload = str(int(payload) / 1000)
    message = str(mac) + "/voc/" + str(sensorid) +"/sl/" + str(payload)
    ser_msg_send(message)

def mqtt_msg_handler_sensor_pm2_5_sleep(mac, sensorid, payload):
    payload = str(int(payload) / 1000)
    message = str(mac) + "/pm2_5/" + str(sensorid) +"/sl/" + str(payload)
    ser_msg_send(message)

def mqtt_msg_handler_sensor_pm10_sleep(mac, sensorid, payload):
    payload = str(int(payload) / 1000)
    message = str(mac) + "/pm10/" + str(sensorid) +"/sl/" + str(payload)
    ser_msg_send(message)

def mqtt_msg_handler_sensor_illuminance_sleep(mac, sensorid, payload):
    payload = str(int(payload) / 1000)
    message = str(mac) + "/lux/" + str(sensorid) +"/sl/" + str(payload)
    ser_msg_send(message)

def mqtt_msg_handler_sensor_pressure_sleep(mac, sensorid, payload):
    payload = str(int(payload) / 1000)
    message = str(mac) + "/pr/" + str(sensorid) +"/sl/" + str(payload)
    ser_msg_send(message)


def mqtt_msg_handler_sensor_bulb_switch(mac, sensorid, payload):
    if payload == "false":
        value = 0
    elif payload == "true":
        value = 1
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_sensor_bulb_switch: invalid payload <" + payload + ">")
        return

    message = str(mac) + "/blb/set/sw/" + str(value)
    ser_msg_send(message)

def mqtt_msg_handler_sensor_bulb_level(mac, sensorid, payload):
    try:
        value = int(payload)
    except: 
        LOG(SYSLOG_ERR, "mqtt_msg_handler_sensor_bulb_level: invalid payload <" + payload + ">")
        return

    if value >= 0 and value <= 100:
        message = str(mac) + "/blb/set/lv/" + str(value)
        ser_msg_send(message)
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_sensor_bulb_level: invalid payload <" + payload + ">")
        return

def mqtt_msg_handler_sensor_bulb_query(mac, sensorid, payload):
    if payload == "all":
        message = str(mac) + "/blb/qry/all/" + payload
        ser_msg_send(message) 
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_sensor_bulb_query: invalid payload <" + payload + ">")
        return

def mqtt_msg_handler_sensor_colorbulb_switch(mac, sensorid, payload):
    if payload == "false":
        value = 0
    elif payload == "true":
        value = 1
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_sensor_colorbulb_switch: invalid payload <" + payload + ">")
        return

    message = str(mac) + "/cblb/set/sw/" + str(value)
    ser_msg_send(message)

def mqtt_msg_handler_sensor_colorbulb_level(mac, sensorid, payload):
    try:
        value = int(payload)
    except:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_sensor_colorbulb_level: invalid payload <" + payload + ">")
        return

    if value >= 0 and value <= 100:
        message = str(mac) + "/cblb/set/lv/" + str(value)
        ser_msg_send(message)
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_sensor_colorbulb_level: invalid payload <" + payload + ">")
        return

def mqtt_msg_handler_sensor_colorbulb_hue(mac, sensorid, payload):
    try:
        value = int(payload)
    except:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_sensor_colorbulb_hue: invalid payload <" + payload + ">")
        return

    if value >= 0 and value <= 360:
        message = str(mac) + "/cblb/set/hue/" + str(value)
        ser_msg_send(message)
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_sensor_colorbulb_hue: invalid payload <" + payload + ">")
        return

def mqtt_msg_handler_sensor_colorbulb_saturation(mac, sensorid, payload):
    try:
        value = int(payload)
    except:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_sensor_colorbulb_saturation: invalid payload <" + payload + ">")
        return

    if value >= 0 and value <= 100:
        message = str(mac) + "/cblb/set/sat/" + str(value)
        ser_msg_send(message)
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_sensor_colorbulb_saturation: invalid payload <" + payload + ">")
        return

def mqtt_msg_handler_sensor_colorbulb_hsv(mac, sensorid, payload):
    message = str(mac) + "/cblb/set/hsv/" + str(payload) + _transitionTime
    ser_msg_send(message)

def mqtt_msg_handler_sensor_colorbulb_temperature(mac, sensorid, payload):
    message = str(mac) + "/cblb/set/ctemp/" + str(payload)
    ser_msg_send(message)

def mqtt_msg_handler_sensor_colorbulb_query(mac, sensorid, payload):
    if payload == "all":
        message = str(mac) + "/cblb/qry/all/" + payload
        ser_msg_send(message)
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_sensor_colorbulb_query: invalid payload <" + payload + ">")
        return

def init_msg_hendler_serial():
    message = "0/ready/0/0/0"
    #message = a2s([0xFE, 0x02, 0x29, 0x00, 0x08, 0x44, 0x67])
    ser_msg_send(message)

def mqtt_msg_handler_gateway_led(gwid, ledid, payload):
    if payload == "OFF":
        led_off(ledid)
    elif payload == "ON":
        led_on(ledid)
    elif payload == "BLINK":
        led_blink(ledid, 1000, 1000)
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_gateway_led: invalid payload <" + payload + ">")

def mqtt_msg_handler_gateway_reset(gwid, resetid, payload):
    LOG(SYSLOG_DBG, "mqtt_msg_handler_gateway_reset: debug <" + payload + ">")

    if payload == "true":
        reset_zigbee("APP")

    elif payload == "sensor_delete":
        message = "0/ready/sensor_delete/0/0"
        ser_msg_send(message)

    elif payload == "sensor_rewrite":
        message = "0/ready/sensor_rewrite/0/0"
        ser_msg_send(message)

    elif payload == "sensor_print":
        message = "0/ready/sensor_print/0/0"
        ser_msg_send(message)

    elif payload == "network_reset":
        message = "0/ready/network_reset/0/0"
        ser_msg_send(message)

    elif payload == "reset":
        message = "0/ready/reset/0/0"
        ser_msg_send(message)

    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_gateway_reset: invalid payload <" + payload + ">") 

def mqtt_msg_handler_gateway_ping(gwid, pingid, payload):
    LOG(SYSLOG_DBG, "mqtt_msg_handler_gateway_ping: debug <" + payload + ">")

    if payload == "ping":
        message = "0/ready/ping/0/0"
        ser_msg_send(message)

    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_gateway_ping: invalid payload <" + payload + ">")

def mqtt_msg_handler_gateway_zdo(subcmd, payload):
    LOG(SYSLOG_DBG, "mqtt_msg_handler_gateway_zdo: debug <" + payload + ">")
    message = None

    if subcmd == "ieee_req":
        message = "0/ready/zdo/ieee/" + payload
    elif subcmd == "lqi_req":
        message = "0/ready/zdo/lqi/" + payload
    elif subcmd == "pid_req":
        message = "0/ready/zdo/pid/" + payload
    elif subcmd == "ch_req":
        message = "0/ready/zdo/ch/" + payload
    elif subcmd == "rm_child_req":
        message = "0/ready/zdo/rm_child/" + payload
    elif subcmd == "join_req":
        message = "0/ready/zdo/join/" + payload
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_gateway_zdo: invalid subcommand <" + subcmd + ">")

    if message is not None:
        ser_msg_send(message)


def mqtt_msg_handler_gateway_loglevel(payload):
    global SYSLOG_SEVERITY
    global SYSLOG_SEVERITY_MAPPING
    LOG(SYSLOG_DBG, "mqtt_msg_handler_gateway_loglevel: <" + payload + ">")

    try:
        severity = int(payload)
        if severity <= SYSLOG_ERR:
            SYSLOG_SEVERITY = severity
        else:
            LOG(SYSLOG_ERR, "mqtt_msg_handler_gateway_loglevel: invalid log level value <" + str(severity) + ">")
    except ValueError:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_gateway_loglevel: invalid log level format <" + str(payload) + ">")


def mqtt_msg_handler_gateway_sbl(gwid, sblid, payload):
    LOG(SYSLOG_DBG, "mqtt_msg_handler_gateway_sbl: debug <" + payload + ">")
    global _mqttc
    global _serial_port
    global _uart_port

    if payload.split(".")[1] == "bin":
        reset_zigbee("SBL")
        #_mqttc.disconnect()
        _serial_port.close()

        #Two ways of calling sbl
        #sbl.main(["-i", "blinky.bin"])

        cmd = './sbl.py -i ' + payload
        cmd_status = os.system(cmd) # returns the exit status

        _serial_port = serial.Serial(_uart_port, baudrate=115200, timeout=15)
        #_mqttc.reconnect()
    else:
        LOG(SYSLOG_ERR, "mqtt_msg_handler_gateway_sbl: payload must be .bin filename <" + payload + ">")  

def mqtt_msg_handler_gateway_ota(gwid, otaid, payload):
    LOG(SYSLOG_DBG, "mqtt_msg_handler_gateway_ota: debug <" + payload + ">")
    global _ota_allowed
    global _serial_port

    if payload == "START":
        message = "0/ota/0/0/1"
        _ota_allowed = 1
        ser_msg_send(message)
        return

    elif payload == "STOP":
        message = "0/ota/0/0/0"
        _ota_allowed = 0
        ser_msg_send(message)
        return

    if otaid == "image_notify":
        #payload_format: address,version_id
        #payload_format: 85:C6,01:02:04:12
        try:
            address = payload.split(",")[0].split(":")
            address0 = int(address[0], 16)
            address1 = int(address[1], 16)

            versionid  = payload.split(",")[1].split(":")
            versionid0 = int(versionid[0], 16)
            versionid1 = int(versionid[1], 16)
            versionid2 = int(versionid[2], 16)
            versionid3 = int(versionid[3], 16)

            IMAGE_NOTIFY_REQ = 1
            BULB_ENDPOINT = 3
            OSRAM_MANUFACTURER_ID0 = 0x0C
            OSRAM_MANUFACTURER_ID1 = 0x11

            OSRAM_TYPE_ID0 = 0x00
            OSRAM_TYPE_ID1 = 0x06

            cmd0 = APP_MSG_CMD0
            cmd1 = APP_MSG_CMD1

            mt_msg = [OTA_ENDPOINT, IMAGE_NOTIFY_REQ, address1, address0, BULB_ENDPOINT, 0x00, 0x00, OSRAM_MANUFACTURER_ID0, OSRAM_MANUFACTURER_ID1, OSRAM_TYPE_ID1, OSRAM_TYPE_ID0, versionid3, versionid2, versionid1, versionid0]

            msg = (cmd0, cmd1, mt_msg)
            #otaserv.mt_send_message(msg, _serial_port)

        except:
            print("WRONG FORMAT")
            pass

def mqtt_msg_handler_gateway_swdl(gwid, swdlid, payload):
    LOG(SYSLOG_DBG, "mqtt_msg_handler_gateway_swdl: debug <" + payload + ">")
    if payload == "START":
        led_on("5")

    elif payload == "END":
        led_off("5")

def mqtt_msg_handler_gateway_lqi(gwid, lqiid, payload):
    LOG(SYSLOG_DBG, "mqtt_msg_handler_gateway_lqi: debug <" + payload + ">")
    if payload == "START":
        message = str(gwid) + "/hw/" + str(lqiid) +"/lqi/" + str(1)
        ser_msg_send(message)

def gw_send_serial():
    global _gw_serial_sent
    global _gwid

    if _gw_serial_sent:
        # message already sent, nothing to do; we probably reconnected
        return

    mqtt_msg_publish("smarthome/gateway/" + _gwid + "/hw/serial", _gwid)
    _gw_serial_sent = True


def gw_send_revision():
    global _gw_revision_sent
    global _gwid

    if _gw_revision_sent:
        # message already sent, nothing to do; we probably reconnected
        return

    mqtt_msg_publish("smarthome/gateway/" + _gwid + "/hw/revision", "GWMB:RFMA")
    _gw_revision_sent = True


def gw_coord_mac_send():
    global _gwid
    global _coord_mac_sent
    global _coord_nid

    if _coord_mac_sent:
        # message already sent
        LOG(SYSLOG_WRN, "gw_coord_mac_send: message already sent")
        return

    if _coord_nid == "":
        # coordinator haven't announced itself yet
        LOG(SYSLOG_WRN, "gw_coord_mac_send: coord nid not known")
        return

    mac = get_nid_mac(_coord_nid)
    if mac is None:
        # coordinator node haven't send its mac address yet
        LOG(SYSLOG_WRN, "gw_coord_mac_send: coord mac not known")
        return

    mqtt_msg_publish("smarthome/gateway/" + _gwid + "/hw/zigbee/MAC", mac)
    _coord_mac_sent = True


def gw_coord_nwk_send():
    global _gwid
    global _coord_nwk_sent
    global _coord_nid

    if _coord_nwk_sent:
        # message already sent
        return

    if _coord_nid == "":
        # coordinator haven't announced itself yet
        return

    nwk = get_nid_nwk(_coord_nid)
    if nwk is None:
        # coordinator node haven't send its nwk address yet
        return

    mqtt_msg_publish("smarthome/gateway/" + _gwid + "/hw/zigbee/NWK", nwk)
    _coord_nwk_sent = True


class RepeatedTimer(object):
    def __init__(self, interval, function, *args, **kwargs):
        self._timer     = None
        self.interval   = interval
        self.function   = function
        self.args       = args
        self.kwargs     = kwargs
        self.is_running = False
        self.start()

    def _run(self):
        self.is_running = False
        self.start()
        self.function(*self.args, **self.kwargs)

    def start(self):
        if not self.is_running:
            self._timer = Timer(self.interval, self._run)
            self._timer.start()
            self.is_running = True

    def stop(self):
        self._timer.cancel()
        self.is_running = False


class SingleShotTimer(object):
    def __init__(self, interval, function, *args, **kwargs):
        self._timer     = None
        self.interval   = interval
        self.function   = function
        self.args       = args
        self.kwargs     = kwargs
        self.is_running = False
        self.start()

    def _run(self):
        self.is_running = False
        self.function(*self.args, **self.kwargs)

    def start(self):
        if not self.is_running:
            self._timer = Timer(self.interval, self._run)
            self._timer.start()
            self.is_running = True

    def stop(self):
        self._timer.cancel()
        self.is_running = False



def led_init():
    global _gw_rev
    global _gwmb_led1_addr
    global _gwmb_led2_addr
    global _gwmc_led1_addr
    global _gwmc_led2_addr
    global _gwmc_led3_addr
    global _gwmc_led4_addr
    global _gwmc_led5_addr

    # configure LED pins for output
    if _gw_rev == "GWMB":
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(_gwmb_led1_addr, GPIO.OUT)
        GPIO.setup(_gwmb_led2_addr, GPIO.OUT)
    elif _gw_rev == "GWMC":
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(_gwmc_led1_addr, GPIO.OUT)
        GPIO.setup(_gwmc_led2_addr, GPIO.OUT)
        GPIO.setup(_gwmc_led3_addr, GPIO.OUT)
        GPIO.setup(_gwmc_led4_addr, GPIO.IN)
        GPIO.setup(_gwmc_led5_addr, GPIO.OUT)

    #configure reset pin as output, and preform reset of CC2530
    reset_zigbee("SBL")

    if _gw_rev == "GWMB":
        led_off("1")
        led_off("2")
    elif _gw_rev == "GWMC":
        led_off("1")
        led_off("2")
        led_on("3")
        led_off("5")

def led_exit():
    global _gw_rev

    if _gw_rev == "GWMB":
        led_off("1")
        led_timer_stop("1")
        led_off("2")
        led_timer_stop("2")
    elif _gw_rev == "GWMC":
        led_off("1")
        led_timer_stop("1")
        led_off("2")
        led_timer_stop("2")
        led_off("3")
        led_timer_stop("3")
        led_off("4")
        led_timer_stop("4")
        led_off("5")
        led_timer_stop("5")



def led_timer_callback(led_id, on, off):
    # on and off are expressed in milliseconds
    global _gw_rev
    global _gwmb_led1_timer
    global _gwmb_led2_timer
    global _gwmc_led1_timer
    global _gwmc_led2_timer
    global _gwmc_led3_timer
    global _gwmc_led4_timer
    global _gwmc_led5_timer

    LOG(SYSLOG_DBG, "BLINK id=" + str(led_id) + ", on=" + str(on) + ", off=" + str(off))

    if _gw_rev == "GWMB":
        if led_id == "1":
            if GPIO.input(_gwmb_led1_addr):
                _gwmb_led1_timer = SingleShotTimer(off/1000, led_timer_callback, led_id, on, off)
            else:
                _gwmb_led1_timer = SingleShotTimer(on/1000, led_timer_callback, led_id, on, off)
            led_toggle(led_id)
        elif led_id == "2":
            if GPIO.input(_gwmb_led2_addr):
                _gwmb_led2_timer = SingleShotTimer(off/1000, led_timer_callback, led_id, on, off)
            else:
                _gwmb_led2_timer = SingleShotTimer(on/1000, led_timer_callback, led_id, on, off)
            led_toggle(led_id)
    elif _gw_rev == "GWMC":
        if led_id == "1":
            if GPIO.input(_gwmc_led1_addr):
                _gwmc_led1_timer = SingleShotTimer(off/1000, led_timer_callback, led_id, on, off)
            else:
                _gwmc_led1_timer = SingleShotTimer(on/1000, led_timer_callback, led_id, on, off)
            led_toggle(led_id)
        elif led_id == "2":
            if GPIO.input(_gwmc_led2_addr):
                _gwmc_led2_timer = SingleShotTimer(off/1000, led_timer_callback, led_id, on, off)
            else:
                _gwmc_led2_timer = SingleShotTimer(on/1000, led_timer_callback, led_id, on, off)
            led_toggle(led_id)
        elif led_id == "3":
            if GPIO.input(_gwmc_led3_addr):
                _gwmc_led3_timer = SingleShotTimer(off/1000, led_timer_callback, led_id, on, off)
            else:
                _gwmc_led3_timer = SingleShotTimer(on/1000, led_timer_callback, led_id, on, off)
            led_toggle(led_id)
        elif led_id == "4":
            if GPIO.input(_gwmc_led4_addr):
                _gwmc_led4_timer = SingleShotTimer(off/1000, led_timer_callback, led_id, on, off)
            else:
                _gwmc_led4_timer = SingleShotTimer(on/1000, led_timer_callback, led_id, on, off)
            led_toggle(led_id)
        elif led_id == "5":
            if GPIO.input(_gwmc_led5_addr):
                _gwmc_led5_timer = SingleShotTimer(off/1000, led_timer_callback, led_id, on, off)
            else:
                _gwmc_led5_timer = SingleShotTimer(on/1000, led_timer_callback, led_id, on, off)
            led_toggle(led_id)


def led_timer_stop(led_id):
    global _gw_rev
    global _gwmb_led1_timer
    global _gwmb_led2_timer
    global _gwmc_led1_timer
    global _gwmc_led2_timer
    global _gwmc_led3_timer
    global _gwmc_led4_timer
    global _gwmc_led5_timer

    if _gw_rev == "GWMB":
        if led_id == "1":
            if _gwmb_led1_timer is not None:
                _gwmb_led1_timer.stop()
        elif led_id == "2":
            if _gwmb_led2_timer is not None:
                _gwmb_led2_timer.stop()
    elif _gw_rev == "GWMC":
        if led_id == "1":
            if _gwmc_led1_timer is not None:
                _gwmc_led1_timer.stop()
        elif led_id == "2":
            if _gwmc_led2_timer is not None:
                _gwmc_led2_timer.stop()
        elif led_id == "3":
            if _gwmc_led3_timer is not None:
                _gwmc_led3_timer.stop()
        elif led_id == "4":
            if _gwmc_led4_timer is not None:
                _gwmc_led4_timer.stop()
        elif led_id == "5":
            if _gwmc_led5_timer is not None:
                _gwmc_led5_timer.stop()


def led_on(led_id):
    global _gw_rev
    global _gwmb_led1_addr
    global _gwmb_led2_addr
    global _gwmc_led1_addr
    global _gwmc_led2_addr
    global _gwmc_led3_addr
    global _gwmc_led4_addr
    global _gwmc_led5_addr
    global _gwmb_led1_timer
    global _gwmb_led2_timer
    global _gwmc_led1_timer
    global _gwmc_led2_timer
    global _gwmc_led3_timer
    global _gwmc_led4_timer
    global _gwmc_led5_timer

    led_timer_stop(led_id)

    if _gw_rev == "GWMB":
        if led_id == "1":
            GPIO.output(_gwmb_led1_addr, True)
        elif led_id == "2":
            GPIO.output(_gwmb_led2_addr, True)
    elif _gw_rev == "GWMC":
        if led_id == "1":
            GPIO.output(_gwmc_led1_addr, False)
        elif led_id == "2":
            GPIO.output(_gwmc_led2_addr, False)
        elif led_id == "3":
            GPIO.output(_gwmc_led3_addr, False)
        elif led_id == "4":
            GPIO.output(_gwmc_led4_addr, False)
        elif led_id == "5":
            GPIO.output(_gwmc_led5_addr, False)

def led_off(led_id):
    global _gw_rev
    global _gwmb_led1_addr
    global _gwmb_led2_addr
    global _gwmc_led1_addr
    global _gwmc_led2_addr
    global _gwmc_led3_addr
    global _gwmc_led4_addr
    global _gwmc_led5_addr
    global _gwmb_led1_timer
    global _gwmb_led2_timer
    global _gwmc_led1_timer
    global _gwmc_led2_timer
    global _gwmc_led3_timer
    global _gwmc_led4_timer
    global _gwmc_led5_timer

    led_timer_stop(led_id)

    if _gw_rev == "GWMB":
        if led_id == "1":
            GPIO.output(_gwmb_led1_addr, False)
        elif led_id == "2":
            GPIO.output(_gwmb_led2_addr, False)
    elif _gw_rev == "GWMC":
        if led_id == "1":
            GPIO.output(_gwmc_led1_addr, True)
        elif led_id == "2":
            GPIO.output(_gwmc_led2_addr, True)
        elif led_id == "3":
            GPIO.output(_gwmc_led3_addr, True)
        elif led_id == "4":
            GPIO.output(_gwmc_led4_addr, True)
        elif led_id == "5":
            GPIO.output(_gwmc_led5_addr, True)


def led_toggle(led_id):
    global _gw_rev
    global _gwmb_led1_addr
    global _gwmb_led2_addr
    global _gwmc_led1_addr
    global _gwmc_led2_addr
    global _gwmc_led3_addr
    global _gwmc_led4_addr
    global _gwmc_led5_addr
    global _gwmb_led1_timer
    global _gwmb_led2_timer
    global _gwmc_led1_timer
    global _gwmc_led2_timer
    global _gwmc_led3_timer
    global _gwmc_led4_timer
    global _gwmc_led5_timer

    if _gw_rev == "GWMB":
        if led_id == "1":
            GPIO.output(_gwmb_led1_addr, not GPIO.input(_gwmb_led1_addr))
        elif led_id == "2":
            GPIO.output(_gwmb_led2_addr, not GPIO.input(_gwmb_led2_addr))
    elif _gw_rev == "GWMC":
        if led_id == "1":
            GPIO.output(_gwmc_led1_addr, not GPIO.input(_gwmc_led1_addr))
        elif led_id == "2":
            GPIO.output(_gwmc_led2_addr, not GPIO.input(_gwmc_led2_addr))
        elif led_id == "3":
            GPIO.output(_gwmc_led3_addr, not GPIO.input(_gwmc_led3_addr))
        elif led_id == "4":
            GPIO.output(_gwmc_led4_addr, not GPIO.input(_gwmc_led4_addr))
        elif led_id == "5":
            GPIO.output(_gwmc_led5_addr, not GPIO.input(_gwmc_led5_addr))


def led_blink(led_id, on, off):
    # on and off are expressed in milliseconds
    global _gw_rev
    global _gwmb_led1_addr
    global _gwmb_led2_addr
    global _gwmc_led1_addr
    global _gwmc_led2_addr
    global _gwmc_led3_addr
    global _gwmc_led4_addr
    global _gwmc_led5_addr
    global _gwmb_led1_timer
    global _gwmb_led2_timer
    global _gwmc_led1_timer
    global _gwmc_led2_timer
    global _gwmc_led3_timer
    global _gwmc_led4_timer
    global _gwmc_led5_timer

    led_timer_stop(led_id)

    if _gw_rev == "GWMB":
        if led_id == "1":
            _gwmb_led1_timer = SingleShotTimer(on/1000, led_timer_callback, led_id, on, off)
        elif led_id == "2":
            _gwmb_led2_timer = SingleShotTimer(on/1000, led_timer_callback, led_id, on, off)
    elif _gw_rev == "GWMC":
        if led_id == "1":
            _gwmc_led1_timer = SingleShotTimer(on/1000, led_timer_callback, led_id, on, off)
        elif led_id == "2":
            _gwmc_led2_timer = SingleShotTimer(on/1000, led_timer_callback, led_id, on, off)
        elif led_id == "3":
            _gwmc_led3_timer = SingleShotTimer(on/1000, led_timer_callback, led_id, on, off)
        elif led_id == "4":
            _gwmc_led4_timer = SingleShotTimer(on/1000, led_timer_callback, led_id, on, off)
        elif led_id == "5":
            _gwmc_led5_timer = SingleShotTimer(on/1000, led_timer_callback, led_id, on, off)

    return

def reset_zigbee(type):
    global _serial_port
    global _uart_port
    #configure reset pin as output, and preform reset of CC2530
    GPIO.setmode(GPIO.BOARD)
    GPIO.setup(13, GPIO.OUT)
    GPIO.output(13,0)
    time.sleep(1)
    GPIO.output(13,1)
    time.sleep(1)

    LOG(SYSLOG_INF, "ZIGBEE RESET")

    if type == "SBL":
        return
    elif type == "APP":
        #send force run command
        cmd = b'\xef'

        try:
            if _serial_port.isOpen():
                _serial_port.write(cmd)
        except serial.SerialException as e:
            LOG(SYSLOG_WRN, "Serial exception: " + str(e))
        except TypeError as e:
            LOG(SYSLOG_WRN, "UART Disconnected: " + str(e))
            _serial_port.close()
            _serial_port = serial.Serial(_uart_port, baudrate=uart_baudrate, timeout=15)

        LOG(SYSLOG_INF, "poslan kod za izlazak iz bootloadera")

        #wait a moment so bootloader can start app
        time.sleep(1)

    return

def sblCheck():
    global _coord_nid

    if _coord_nid == "":
        LOG(SYSLOG_INF, "Coordinator didn't send its MAC in 10s, start sbl")
        #sbl_handler(SBL_FORCE)

    return

def ping_timer_callback(arg1, arg2, arg3):
    global _ping_timer_timeout_flag
    message = "0/ready/ping/0/0"
    LOG(SYSLOG_DBG, "PING SEND")
    ser_msg_send(message)
    _ping_timer_timeout = SingleShotTimer(1, ping_timer_timeout_callback, 0, 0, 0)
    _ping_timer = SingleShotTimer(PING_TIMER_TIMEOUT, ping_timer_callback, 0, 0, 0)
    return

def ping_timer_timeout_callback(arg1, arg2, arg3):
    global _ping_timer_timeout_flag
    if _ping_timer_timeout_flag:
        _ping_timer_timeout_flag = 0
        LOG(SYSLOG_DBG, "PING RETURN RECEIVED")
    else:
        reset_zigbee("SBL")
    return

def usage():
    print("Zigbee node mqtt client")
    print("Usage is:" + sys.argv[0] + " [options]")
    print("Options are:")
    print("--<t>ty tty device (/dev/ttyAMA0)")
    print("--<b>aud baudrate (38400)")
    print("--b<r>oker mqtt broker address (messagesight.demos.ibm.com)")
    print("--<p>ort mqtt broker port (1883)")

def main(argv):

    global _serial_port
    global _mqttc
    global _gwid
    global _uart_port

    # default parameters
    _uart_port = "/dev/ttyAMA0"
    uart_baudrate = 115200
    broker_address = "localhost"
    broker_port = 1883

    #CTS/RTS pins (16,17) to alt 3 mode
    cmd_status = 0

#    cmd = './gpio_alt -p 16 -f 3'
#    cmd_status = os.system(cmd) # returns the exit status
#    if cmd_status != 0:
#        LOG(SYSLOG_ERR, "os.system('./gpio_alt -p 16 -f 3'): invalid exit status <" + str(cmd_status) + ">")

#    cmd = './gpio_alt -p 17 -f 3'
#    cmd_status = os.system(cmd) # returns the exit status
#    if cmd_status != 0:
#        LOG(SYSLOG_ERR, "os.system('./gpio_alt -p 17 -f 3'): invalid exit status <" + str(cmd_status) + ">")

    try:
        opts, args = getopt.getopt(argv,"ht:b:r:p:",["help","tty=", "baud=", "broker=", "port="])
    except getopt.GetoptError:
        usage()
        sys.exit(2)
    for opt, arg in opts:
        if opt in ('-h', "--help"):
            usage()
            sys.exit()
        elif opt in ("-t", "--tty"):
            _uart_port = "/dev/" + arg
        elif opt in ("-b", "--baud"):
            uart_baudrate = arg
        elif opt in ("-r", "--broker"):
            broker_address = arg
        elif opt in ("-p", "--port"):
            broker_port = arg


    # install SIGINT signal handler
    signal.signal(signal.SIGINT, sigint_handler)

    led_init()

    _gwid = get_gwid()

    LOG(SYSLOG_INF, "gw node id: " + str(_gwid))


    #first open 115200 uart connection for SBL
    _serial_port = serial.Serial(_uart_port, baudrate=uart_baudrate, timeout=15)

    #send force run command
    cmd = b'\xef'
    _serial_port.write (cmd)
    LOG(SYSLOG_INF, "poslan kod za izlazak iz bootloadera")

    #wait a moment so bootloader can start app
    time.sleep(1)

    _mqttc = mqtt.Client()
    _mqttc.on_connect = on_connect
    _mqttc.on_message = on_message
    _mqttc.on_publish = on_publish

    _mqttc.connect(broker_address, port=broker_port, keepalive=60)

    #start the background thread to handle network traffic
    _mqttc.loop_start()

    time.sleep(1)
    init_msg_hendler_serial()

    #if gw doesnt send anything for 10 sec, run sbl
    sblTimer = Timer(10,sblCheck)
    sblTimer.start()

    _ping_timer = SingleShotTimer(PING_TIMER_TIMEOUT, ping_timer_callback, 0, 0, 0)

    msgStart = 0

    while True:
        try:
            if _serial_port.isOpen():
                msgStart = _serial_port.read()
        except serial.SerialException as e:
            LOG(SYSLOG_WRN, "Serial exception: " + str(e))
        except TypeError as e:
                LOG(SYSLOG_WRN, "UART Disconnected: " + str(e))
                _serial_port.close()
                _serial_port = serial.Serial(_uart_port, baudrate=uart_baudrate, timeout=15)

        #MT messages
        if msgStart == b'\xfe':
            #print("MT_MESSAGE:")
            try:
                if _serial_port.isOpen():
                    MTdataLen = _serial_port.read()
                    MTmessage = b'\xfe' + MTdataLen + _serial_port.read(MTdataLen[0] + 3)
            except serial.SerialException as e:
                LOG(SYSLOG_WRN, "Serial exception: " + str(e))
            except TypeError as e:
                LOG(SYSLOG_WRN, "UART Disconnected:" + str(e))
                _serial_port.close()
                _serial_port = serial.Serial(_uart_port, baudrate=uart_baudrate, timeout=15)

            s=list(MTmessage)
            LOG(SYSLOG_DBG, "received mt message: <" + ' '.join('0x{:02x}'.format(x) for x in s) + ">")

            #if _ota_allowed == 1:
                #msg = otaserv.mt_receive_message(MTmessage)
                #print(msg)
                #if msg:
                    #otaserv.mt_handle_message(msg, _serial_port)

        #First terminating charactor, only once on beggining
        elif msgStart == b'\n':
            LOG(SYSLOG_INF, "FIRST READY MESSAGE RECEIVED")

        #Regular sensor messages
        else:
            try:
                if _serial_port.isOpen():
                    message = msgStart + _serial_port.readline()
                    led_on("1")
                    process_serial_message(message, _mqttc)
                    led_off("1")
            except serial.SerialException as e:
                LOG(SYSLOG_WRN, "Serial exception" + str(e))
            except TypeError as e:
                LOG(SYSLOG_WRN, "UART Disconnected:" + str(e))
                _serial_port.close()
                _serial_port = serial.Serial(_uart_port, baudrate=uart_baudrate, timeout=15)



if __name__=="__main__":
    main(sys.argv[1:])
