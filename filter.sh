#!/bin/bash

while true
do
    sed -i "" '/$GPRMC/!p' output.txt 
    sed -i '' '/[CGARgj]/d' output.txt 

done
