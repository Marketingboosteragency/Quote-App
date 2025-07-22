#!/usr/bin/env bash
# Exit on error
set -o errexit

# Instalar las dependencias de Python
pip install -r requirements.txt

# Crear los directorios en el disco persistente.
# La ruta /mnt/data es donde Render monta el disco.
# El flag -p asegura que no falle si los directorios ya existen.
mkdir -p /mnt/data/uploads
mkdir -p /mnt/data/cotizaciones