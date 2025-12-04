// main.js

document.addEventListener("DOMContentLoaded", () => {
  // pompy
  FurnaceUI.pumps.set("cwu", true);
  FurnaceUI.pumps.set("co", false);

  // ślimak
  FurnaceUI.auger.set(false);

  // dmuchawa
  FurnaceUI.blower.setPower(0);

  // temperatury
  FurnaceUI.temps.setFurnace(58.3);
  FurnaceUI.temps.setRadiators(32);
  FurnaceUI.temps.setMixer(38);
  FurnaceUI.temps.setAuger(28);

  // paliwo i korekcja
  FurnaceUI.fuel.setKg(130);
  FurnaceUI.corrections.setAugerSeconds(6);

  // status
  FurnaceUI.ui.setStatus("Praca automatyczna – dogrzewanie CWU");
});
