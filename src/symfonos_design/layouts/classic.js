// symfonOS "Klassisch": eine Leiste unten (Windows-/KDE-typisch)
panels().forEach(function (p) { p.remove(); });
var p = new Panel;
p.location = "bottom";
p.height = Math.round(gridUnit * 2.4);
p.floating = true;
p.addWidget("org.kde.plasma.kickoff");
p.addWidget("org.kde.plasma.pager");
var t = p.addWidget("org.kde.plasma.icontasks");
p.addWidget("org.kde.plasma.marginsseparator");
p.addWidget("org.kde.plasma.systemtray");
p.addWidget("org.kde.plasma.digitalclock");
p.addWidget("org.kde.plasma.showdesktop");
