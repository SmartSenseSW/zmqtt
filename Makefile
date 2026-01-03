# Project Variables
PACKAGE_NAME := zmqtt-service
VERSION := 1.0.2
ARCH := all
DEB_NAME := $(PACKAGE_NAME)_$(VERSION)_$(ARCH).deb
BUILD_DIR := zmqtt-pkg

.PHONY: clean deb

# Default target
all: deb

# Clean up previous builds and temporary files
clean:
	rm -f *.deb
	# Remove the auto-generated python files from the build dir
	rm -f $(BUILD_DIR)/opt/zmqtt/*.py
	# Remove the compressed changelog from the build dir
	rm -f $(BUILD_DIR)/usr/share/doc/$(PACKAGE_NAME)/changelog.Debian.gz

# Build the .deb package
deb: clean
	@echo "Building $(DEB_NAME)..."

	# 1. Copy Python scripts to the build folder
	cp src/*.py $(BUILD_DIR)/opt/zmqtt/
	chmod 644 $(BUILD_DIR)/opt/zmqtt/*.py

	# 2. Handle the Changelog (Copy -> Rename -> Gzip)
	# Copy the plaintext CHANGELOG to the destination as 'changelog.Debian'
	cp CHANGELOG $(BUILD_DIR)/usr/share/doc/$(PACKAGE_NAME)/changelog.Debian
	# Compress it in place (gzip -n removes timestamps for reproducible builds)
	gzip -9 -n $(BUILD_DIR)/usr/share/doc/$(PACKAGE_NAME)/changelog.Debian

	# 3. Build the package
	dpkg-deb --root-owner-group --build $(BUILD_DIR) $(DEB_NAME)

	@echo "Build complete: $(DEB_NAME)"
