# Veyra · Kit de marca Ciruela

Reconstrucción vectorial basada en el concepto Vértice aprobado. La tipografía se ha adaptado con DejaVu Sans Bold y convertido a trazados: no necesita fuentes externas. No es un calco exacto del render conceptual.

- logo.svg / .png: logo horizontal para fondos claros.
- logo-inverse.svg / .png: logo blanco para fondos oscuros; fondo transparente.
- logo-monochrome.svg / .png: logo monocromático carbón.
- symbol.svg / .png y symbol-inverse.svg / .png: símbolo independiente.
- app-icon.svg / .png: icono con fondo ciruela.
- favicon.svg, favicon.ico y PNG de 16, 32 y 48 px.
- apple-touch-icon.png: 180 px, fondo opaco.
- icon-192.png, icon-512.png y icon-maskable-512.png: iconos para instalación web.
- site.webmanifest: ejemplo de manifiesto; ajusta start_url si la aplicación se publica en una subruta. Incluirlo no añade por sí solo soporte sin conexión.
- integration.html: etiquetas para HTML o index.html de React/Vite.

Conserva la proporción del logo, sin estirarlo. Usa SVG preferentemente. PNG con transparencia para herramientas que no admiten SVG. Mantén alrededor del símbolo un margen mínimo equivalente al 20 % de su altura.

Colores: ciruela #78455F, hover #60364C, carbón #2B2029, marfil #FAF8F6, suave #F0E6EC, bordes #DED5DC.

build.py permite regenerar archivos; requiere fontTools, matplotlib y Pillow. La tipografía base DejaVu se distribuye bajo su licencia incluida. No se ha verificado disponibilidad registral de la marca.
