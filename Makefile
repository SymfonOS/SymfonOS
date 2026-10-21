.PHONY: test lint iso repo wallpaper demo-store screenshots

test:
	QT_QPA_PLATFORM=offscreen python3 -m pytest -q

lint:
	ruff check src tests --select E,F,W --ignore E501,E731,E741
	shellcheck -S warning exam/symfonos-exam iso/build.sh tools/repo-sync.sh system/usr/bin/* system/usr/lib/symfonos/*.sh iso/airootfs/usr/local/bin/*

# Benötigt Arch Linux (oder Container archlinux:latest --privileged) und root
iso:
	sudo ./iso/build.sh out

repo:
	SYP_KEY=$${SYP_KEY:?} ./tools/repo-sync.sh out/repo

wallpaper:
	python3 tools/make-wallpaper.py build/wallpapers
