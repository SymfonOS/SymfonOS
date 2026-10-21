// Fügt die per SYMFONOS_WIDGETS übergebenen Widgets dem ersten Desktop hinzu (ersetzt durch Design-Center)
var wanted = "%WIDGETS%".split(",").filter(function (w) { return w.length > 0; });
var d = desktops()[0];
d.widgets().forEach(function (w) {
  w.currentConfigGroup = ["General"];
  if (w.readConfig("symfonosManaged", "false") === "true") { w.remove(); }
});
var x = 40;
wanted.forEach(function (id) {
  var w = d.addWidget(id, x, 60, 360, 240);
  w.currentConfigGroup = ["General"];
  w.writeConfig("symfonosManaged", "true");
  x += 400;
});
