# Smart Sense Zigbee-MQTT Gateway

## Uvod
Ovaj projekt je nastao nakon gašenja Iskonove usluge **Smart Home**, s ciljem da omogući korisnicima nastavak korištenja originalne opreme u aplikaciji **Home Assistant**. Projekt je prilagodba dijela izvornog koda za komunikaciju sa Zigbee modemom za izvršavanje na trenutno aktualnoj verziji Raspberry Pi OS-a.

## Odricanje od odgovornosti
**Važno:** Ovaj projekt je nezavisna inicijativa otvorenog koda, za koju tvrtke Iskon i Smart-Sense ne preuzimaju nikakvu odgovornost. Hardver je povučen iz prodaje, a službeni popravci i podrška više nisu dostupni. Autor razvija ovaj softver u svoje slobodno vrijeme, bez jamčenja budućih nadogradnji ili tehničke pomoći.

## Preduvjeti
Za prenamjenu sustava trebat će vam sljedeći hardver i znanja:
* **Hardver:** Originalna Smart Sense centralna jedinica i Zigbee senzori.
* **Home Assistant Server:** Zasebno računalo za pokretanje Home Assistanta, obzirom da je Raspberry Pi u centralnoj jedinici preslab
* **Alati:** Križni odvijač za otvaranje kućišta i čitač SD kartica za flashanje Raspberry Pi OS-a.
* **Znanje:** Osnovno poznavanje Linuxa (SSH, terminal naredbe, uređivanje tekstualnih datoteka).

---

## 1. Priprema hardvera i instalacija OS-a

### Korak 1: Rastavljanje centralne jedinice
Otvorite kućište centralne jedinice. Iskon Smart-Home postoji u starijoj varijanti s **Raspberry Pi 1**, ili novijoj s puno bržim **Raspberry Pi 3**, kojeg ćete prepoznati po tome što ima memorijski čip na **stražnjoj strani** pločice. Nakon rastavljanja, naravno, vidi se točan model i na prednjoj strani pločice.

### Korak 2: Vađenje SD kartice
* **Pi 3:** Jednostavno izvucite karticu van.
* **Pi 1:** Lagano gurnite karticu unutra dok ne klikne, zatim otpustite.

### Korak 3: Pristup portovima
Pažljivo iskopčajte i uklonite Smart-Sense Zigbee HAT (pločicu nepravilnog oblika spojenu na 40-pinski Raspberry Pi GPIO port) da biste oslobodili pristup HDMI portu.

### Korak 4: Flashanje operativnog sustava
Snimite aktualni **Raspberry Pi OS Lite** na SD karticu prema [uputama](https://www.raspberrypi.com/software/operating-systems/).
* Koristite **64-bit Lite** verziju za **Pi 3**.
* Koristite **32-bit Lite** verziju za **Pi 1**.

### Korak 5: Prvo pokretanje
1. Umetnite SD karticu, spojite monitor, tipkovnicu i napajanje.
2. Dovršite instalaciju OS-a, kreirajte korisnički račun i provjerite možete li se ulogirati.

---

## 2. Konfiguracija sustava

### Korak 1: Konfiguracija OS-a
Pokrenite alat za konfiguraciju:
```bash
sudo raspi-config
```
1. Omogućite SSH: Idite na `3 Interface Options > I1 SSH > YES`.
2. Konfigurirajte serijski port: Idite na `3 Interface Options > I6 Serial Port`.
```
"Would you like a login shell...?" > NO.
"Would you like the serial port hardware to be enabled?" > YES.
```
3. Završite (`Finish`), ali nemojte još restartati uređaj.

### Korak 2: Onemogućavanje serijske konzole
Pokrenite ovu naredbu:
```bash
sudo systemctl disable serial-getty@ttyAMA0.service
```

### Korak 3: Boot konfiguracija hardvera
Otvorite konfiguracijsku datoteku:
```bash
sudo nano /boot/firmware/config.txt
```
Skrolajte do dna u sekciju `[all]` i dodajte postavke koje nedostaju da bi konfiguracija ovako izgledala:

Za **Raspberry Pi 1**:
```
[all]
enable_uart=1
gpio=16,17=a3
```

Za **Raspberry Pi 3**:
```
[all]
enable_uart=1
dtoverlay=miniuart-bt
gpio=16,17=a3
```

### Korak 4: Restart

```bash
sudo reboot
```

## 3. Provjera i sastavljanje
* Provjerite možete li se spojiti na Raspberry Pi putem SSH s vašeg računala.
* Ugasite Raspberry Pi (`sudo poweroff`) i iskopčajte napajanje.
* Uklonite HDMI kabel i tipkovnicu.
* Vratite Zigbee HAT (pazite da su pinovi poravnati).
* Zatvorite kućište i upalite uređaj.

## 4. Instalacija softvera
Spojite se na uređaj putem SSH i pokrenite sljedeće naredbe:

### Korak 1. Instalacija linux paketa
```bash
sudo apt-get update
sudo apt-get install mosquitto mosquitto-clients python3-serial python3-paho-mqtt python3-rpi-lgpio
```

### Korak 2. Konfiguracija Mosquitto MQTT brokera

Omogućite spajanje na Mosquitto s bilo kojeg mrežnog sučelja:
```bash
curl -OL https://raw.githubusercontent.com/SmartSenseSW/zmqtt/refs/heads/main/all_interfaces.conf
sudo cp all_interfaces.conf /etc/mosquitto/conf.d/
sudo systemctl restart mosquitto.service
```

### Korak 3. Instalacija zmqtt paketa
```bash
curl -OL https://github.com/SmartSenseSW/zmqtt/releases/download/v1.0.0/zmqtt-service_1.0.0_all.deb
sudo apt-get install ./zmqtt-service_1.0.0_all.deb
```

### Korak 4. Promjena konfiguracije servisa (**samo za Raspberry Pi 1**)
**Raspberry Pi 3** ispravno podržava metodu koju koristi LGPIO za identifikaciju pločice, za njega preskočite ovaj korak. Za **Raspberry Pi 1** je potrebno ručno dodati identifikacijski kod u definiciju zmqtt servisa:
```bash
sudo nano /etc/systemd/system/zmqtt.service
```
Dodajte u `[Service]` sekciju liniju:
```
Environment="RPI_LGPIO_REVISION=0x900030"
```
Nakon toga zaustavite i ponovno pokrenite zmqtt servis:
```bash
sudo systemctl daemon-reload
sudo systemctl stop zmqtt.service
sudo systemctl start zmqtt.service
```

## 5. Provjera rada
Prije instalacije Home Assistanta, provjerite komunikaciju Zigbee modema s MQTT brokerom. Pokrenite ovu naredbu za praćenje svih MQTT poruka:

```bash
mosquitto_sub -v -t '#'
```

Očekivani ispis:

*Trebali biste vidjeti niz poruka s centralne jedinice i vaših senzora (temperatura, pokret, baterija, itd.), npr.:*

```
smarthome/node/67161707004B1200/hw/serial FFFFFFFF
smarthome/node/67161707004B1200/hw/zigbee/MAC 67161707004B1200
smarthome/node/67161707004B1200/sensor/temperature/1026/value/actual 19.96
smarthome/node/67161707004B1200/sensor/humidity/1029/value/actual 70.5
smarthome/node/67161707004B1200/sensor/motion/1030/value/status IDLE
smarthome/node/67161707004B1200/battery/voltage 3.23
smarthome/gateway/0EF91607004B1200/hw/zigbee/role coordinator
smarthome/gateway/0EF91607004B1200/sw/zmq_version v2.2.2
smarthome/gateway/0EF91607004B1200/gw/ping 0
```
*(Pritisnite Ctrl+C za prekid praćenja)*

Ako vidite ovakve podatke, centralna jedinica je potpuno funkcionalna i spremna za integraciju s Home Assistantom!

## 6. Instalacija Home Assistanta

Instalirajte Home Assistant na način koji vam odgovara. Ukoliko koristite linux, preporuka je [docker compose](https://www.home-assistant.io/installation/linux/#docker-compose).

Nakon završetka instalacije i dodavanja korisničkog računa, provjerite možete li se ulogirati u Home Assistant dashboard.

### Korak 1. Dodavanje konfiguracije senzora

Automatizirano dodavanje Smart-Sense sezora u Home Assistant trenutno **nije podržano**. Potrebno je ručno prilagoditi [konfiguracijsku datoteku configuration.yaml](config/configuration.yaml).

U priloženoj datoteci kao primjer su dodani senzori:

* combo senzor temperature, vlage i pokreta
* pametna utičnica
* senzor vrata

Svaki senzor ima Zigbee identifikator, koji se može iščitati iz MQTT poruka, npr za *smarthome/node/67161707004B1200/sensor/temperature/1026/value/actual 19.96* to je **67161707004B1200**. U konfiguracijskoj datoteci potrebno je na tri mjesta gdje se spominje identifikator unijeti onaj vlastitog senzora:
```
  unique_id: "67161707004B1200_humidity"
  state_topic: "smarthome/node/67161707004B1200/sensor/humidity/1029/value/actual"
...
  device:
    identifiers: ["67161707004B1200"]
```
Kod utičnice i mikroklime je više senzora objedinjeno pod istim *device* identifikatorom.

Senzore koje ne posjedujete izbrišite iz konfiguracije.

Tako prilagođenu konfiguraciju spremite u `/config` direktorij Home Assistanta, i na web dashboardu u `Developer tools` odaberite `Check and Restart` opciju.

### Korak 2. Dodavanje MQTT integracije

Na web dashboardu odaberite opciju `Settings > Devices & services > Add integration`.

Odaberite MQTT, zatim unesite IP adresu Raspberry Pi-ja iz centralne jedinice. Port je 1883 a username i password ostavite prazne.

Nakon povezivanja, Home Assistant će sve senzore iz konfiguracije koju ste postavili u prethodnom koraku pokušati povezati s pripadajućim MQTT porukama. Ukoliko je sve prošlo u redu, senzori će se pojaviti na dashboardu i njihove vrijednosti će se automatski ažurirati.

## Što dalje?

Proučite [dokumentaciju](https://www.home-assistant.io/getting-started/) Home Assistant sustava.

Isprobano rade sljedeće mogućnosti: 

* [mobilna aplikacija](https://companion.home-assistant.io/docs/getting_started/)
* [integracija kamere](https://www.home-assistant.io/integrations/onvif/)

## Pomoć

Problemi i nejasnoće oko ovih uputa rješavat će se preko GitHub issue trackera.

Službena podrška tvrtke Smart-Sense emailom ili telefonom u vezi ovih uputa **nije dostupna**. 