# Izrada nove verzije

## Potrebni alati

* make
* dpkg-deb
* reprepro

## Debian paket

1. Ažurirati verziju
  * unijeti promjene u [changelog](CHANGELOG)
  * staviti novu verziju u [makefile](Makefile)
  * staviti novu verziju u [debian pkg](zmqtt-pkg/DEBIAN)

2. Izraditi .deb
```
make deb
```

3. Postaviti novi .deb u repozitorij
```
reprepro -b docs includedeb stable ./zmqtt-service_x.y.z_all.deb
```

4. Promjene pushati na git
