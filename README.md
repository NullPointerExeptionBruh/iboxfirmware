# iboxfirmware
Will help to unpack the firmware of the recorder on MStar (maybe not only it)
 1. Install binwalk (for rootfs, initramfs, kernel. Install rustc,python,pip and cargo before it!)
 ```bash
 git clone https://github.com/ReFirmLabs/binwalk.git
 pip install jefferson
 cd binwalk
 sudo apt install build-essentials
 cargo build -release
 cp target/release/binwalk /usr/local/bin
```
2. Unpack firmware
   ```bash
   binwalk -e <firmware file.bin>
   ```
   Here's you firmware!
3. Unpack cgi_config.bin dirrectory
   ```bash
   unpackcgi.py <firmware file.bin> <output dir>
   ```
   "will be continued"
