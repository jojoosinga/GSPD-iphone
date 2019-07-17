#!/bin/bash
python iPhone-gpsd.py > output.txt
sed -i "" '/$GPRMC/!p' output.txt 
sed -i '' '/[CGARgj]/d' output.txt 

