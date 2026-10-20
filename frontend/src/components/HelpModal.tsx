export default function HelpModal({ onClose }: { onClose: () => void }) {
  return (
    <div id="helpm" onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="hbox">
        <div className="hhead"><b>Atajos y ayuda</b><button id="helpx" title="Cerrar (Esc)" onClick={onClose} onMouseDown={(e) => e.preventDefault()}>×</button></div>
        <div className="help">
          <p><b>Etiquetar:</b> presiona una caja (o muévela) y recibe la <b>clase activa</b> (<kbd>1</kbd> piojo, <kbd>2</kbd> mosca). Al presionarla, <b>sus controles aparecen sobre la imagen, junto a la caja</b>: piojo <kbd>L</kbd>, mosca <kbd>M</kbd>, ni uno <kbd>N</kbd>, alternar <kbd>T</kbd>, quitar etiqueta <kbd>U</kbd>, borrar <kbd>X</kbd> y volver a la posición original. <b>Mover:</b> arrastra la caja. <b>Tamaño:</b> arrastra un borde o una esquina de cualquier caja (el cursor cambia).</p>
          <p><b>Revisado</b> <kbd>Espacio</kbd>: lo que no tocaste pasa a «ni piojo ni mosca» y avanza. <kbd>Tab</kbd> salta a la siguiente caja sin etiquetar.</p>
          <p><b>Caja nueva:</b> <kbd>B</kbd> o el botón «＋ Caja» del menú, y arrastra sobre la imagen. <kbd>Esc</kbd> cancela.</p>
          <p><b>Borrar un cuadro</b> (cualquiera, también los del detector): <kbd>X</kbd>, <kbd>Supr</kbd> o <kbd>Retroceso</kbd>. No se pierde: <kbd>Z</kbd> restaura el último, y el aviso junto a la caja y el menú «Foto» ofrecen «Restaurar» (también después de recargar). La evaluación cuenta un cuadro del detector borrado como falso positivo.</p>
          <p><b>Vista:</b> arrastra en vacío o clic derecho = mover la imagen · rueda o lupas <kbd>+</kbd> <kbd>−</kbd> = zoom (con una caja seleccionada, el zoom se centra en ella) · <kbd>F</kbd>, «Ajustar» o clic en el porcentaje = ajustar.</p>
          <p><b>Video original</b>: los controles están siempre bajo la imagen. Al abrir una imagen se ve <b>la imagen con sus cuadros</b> y el video <b>no se reproduce solo</b>. <kbd>P</kbd> reproduce (desde 5 s antes de la foto) o pausa · <kbd>,</kbd> <kbd>.</kbd> un fotograma (<kbd>&lt;</kbd> <kbd>&gt;</kbd> diez) · la barra salta a cualquier punto · <kbd>R</kbd> o «↩ Volver a la imagen» regresa a la imagen con sus cuadros · <kbd>I</kbd> guarda el fotograma que se ve como imagen nueva · <kbd>[</kbd> <kbd>]</kbd> velocidad (0,1× a 4×) · <kbd>O</kbd> cuadros: solo en la foto / tenues / siempre · <kbd>Inicio</kbd> principio de la ventana.</p>
          <p><b>Navegar:</b> <kbd>A</kbd>/<kbd>D</kbd> o flechas (no marcan revisado) · <kbd>S</kbd> siguiente sin revisar · <kbd>G</kbd> ir a una imagen por su número (escribe el número en el indicador del centro y pulsa Enter) · <kbd>?</kbd> esta ayuda. Todo se guarda solo.</p>
        </div>
      </div>
    </div>
  );
}
