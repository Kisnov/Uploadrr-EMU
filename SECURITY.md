# Política de seguridad

## Versiones con soporte

Solo la última: la rama `main` y la imagen de [emuwarez-uploadrr](https://github.com/Kisnov/emuwarez-uploadrr) que
la incluye. No se mantienen versiones antiguas.

## Cómo informar de una vulnerabilidad

Usa el informe privado de GitHub: pestaña **Security** de este repositorio, **Report a vulnerability**
(<https://github.com/Kisnov/Uploadrr-EMU/security/advisories/new>).

No abras un issue público ni publiques el detalle (ni un ejemplo que funcione) antes de que haya arreglo.

Cuenta qué has visto, qué impacto crees que tiene, cómo reproducirlo y la versión o el commit. Si adjuntas un registro,
quita las claves antes.

## Qué cuenta

Solo el camino de eMuwarez (tracker `EMU`). Es el que usa [emuwarez-uploadrr](https://github.com/Kisnov/emuwarez-uploadrr)
y el que se mantiene. El resto de trackers (BLU, ACM, AITHER, PTP…) es código heredado: no se revisa salvo que el fallo
afecte a una subida a eMuwarez.

Por ejemplo:

- Que la clave de la API del tracker, la de TMDb o la de qBittorrent aparezca en la salida, en un registro o en un
  fichero temporal.
- Leer o escribir ficheros fuera de la carpeta que se está subiendo y de `tmp/`.
- Subir algo distinto de lo que se pidió (por ejemplo, un `.torrent` o unas capturas de otro fichero).

Los fallos de otros programas (qBittorrent, el propio tracker, ffmpeg…) repórtalos a sus responsables.

## Claves y datos personales

Nunca pegues la API key de eMuwarez o de TMDb, ni el contenido de `data/config.yaml`, en un informe o un issue. Si
crees que una clave se ha filtrado, cámbiala primero en el servicio al que pertenece y avísame después.

## Qué esperar

El mantenimiento lo lleva una sola persona, sin plazos garantizados. Se confirmará el informe en cuanto se vea y se
arreglará en `main`; los cambios llegan a la aplicación cuando se reconstruye su imagen.
