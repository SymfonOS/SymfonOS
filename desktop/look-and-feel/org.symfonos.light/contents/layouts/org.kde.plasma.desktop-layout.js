// symfonOS "Atelier": schlanke Top-Leiste + schwebendes, zentriertes Dock
panels().forEach(function (p) { p.remove(); });

var top = new Panel;
top.location = "top";
top.height = Math.round(gridUnit * 1.6);
top.floating = false;
top.addWidget("org.kde.plasma.kickoff");
top.addWidget("org.kde.plasma.appmenu");
top.addWidget("org.kde.plasma.panelspacer");
var clock = top.addWidget("org.kde.plasma.digitalclock");
clock.currentConfigGroup = ["Appearance"];
clock.writeConfig("showDate", true);
top.addWidget("org.kde.plasma.panelspacer");
top.addWidget("org.kde.plasma.systemtray");

var dock = new Panel;
dock.location = "bottom";
dock.height = Math.round(gridUnit * 3.2);
dock.alignment = "center";
dock.lengthMode = "fit";
dock.floating = true;
dock.hiding = "dodgewindows";
var tasks = dock.addWidget("org.kde.plasma.icontasks");
tasks.currentConfigGroup = ["General"];
tasks.writeConfig("launchers", [
  "applications:org.kde.dolphin.desktop", "applications:firefox.desktop",
  "applications:org.kde.krita.desktop", "applications:org.inkscape.Inkscape.desktop",
  "applications:gimp.desktop", "applications:blender.desktop",
  "applications:org.kde.kdenlive.desktop", "applications:ardour8.desktop",
  "applications:org.symfonos.Store.desktop", "applications:org.symfonos.Assist.desktop",
  "applications:org.kde.konsole.desktop"
].join(","));
dock.addWidget("org.kde.plasma.trash");
