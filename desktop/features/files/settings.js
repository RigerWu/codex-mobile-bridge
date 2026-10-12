'use strict';
BridgeFileActions.mountSettings(document.querySelector('[data-panel=advanced]'),value=>window.bridgeDesktop.transferSettings(value),{ready:()=>true}).id='file-transfer-settings';
