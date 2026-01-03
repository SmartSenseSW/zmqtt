## Zigbee firmware update

U većini slučajeva **nije potrebno** raditi update Zigbee firmware-a, jer je na Iskonu bila aktivna opcija automatskog ažuriranja i sve centralne jedinice su dobile zadnji firmware:
* v2.9.2 za prvu seriju (Raspberry Pi 1)
* v2.9.5 za drugu seriju (Raspberry Pi 3) - jedina praktična razlika u odnosu na v2.9.2 je u podršci za Heiman-ov senzor dima

Trenutna verzija se vidi u MQTT porukama:
```bash
mosquitto_sub -v -t '#'
...
smarthome/gateway/DDF51607004B1200/sw/rf_version v2.9.5
...
```

## Update procedura za Raspberry Pi 1

Prva serija s Raspberry Pi 1 može se ručno ažurirati na firmware [v2.9.5](GWMC.bin).
```
export RPI_LGPIO_REVISION=900030
curl -OL https://raw.githubusercontent.com/SmartSenseSW/zmqtt/refs/heads/main/firmware/GWMC.bin
sudo systemctl stop zmqtt.service
sudo python3 /opt/zmqtt/sbl.py -i ./GWMC.bin -t /dev/ttyAMA0 -pf
```
Update traje oko minutu i ispisuje puno teksta. Nakon što završi, restartajte zmqtt:
```
sudo systemctl restart zmqtt.service
```
