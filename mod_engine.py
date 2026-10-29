#!/usr/bin/env python3
# Skyrim Mod Engine v0.10 — archive analyzer and per-mod extractor
# ZIP via Python stdlib; 7z/RAR/TAR via installed tools (7z, bsdtar, unrar, unar).
# Also: Data-root detection, compact manifest + per-mod gzip file index, static SKSE checks.
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import mmap
import os
import re
import shutil
import struct
import subprocess
import sys
import textwrap
import xml.etree.ElementTree as ET
import zlib
import zipfile
from pathlib import Path, PurePosixPath
from datetime import datetime

# ===================================================================================
# Interface language: --lang uk|en (remembered in <output>/settings.json), else SME_LANG,
# else the system locale (uk* -> Ukrainian, anything else -> English).
# Messages are written in Ukrainian in the code; tr() maps them to English via _EN.
# ===================================================================================
LANGS = ("uk", "en")
SETTINGS_FILE = "settings.json"
DEFAULT_PROFILE = "Default"

def _argv_value(flag: str) -> str | None:
    argv = sys.argv[1:]
    for i, a in enumerate(argv):
        if a == flag and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None

def _settings_path() -> Path:
    return Path.cwd() / (_argv_value("--output") or "mod_engine_output") / SETTINGS_FILE

def _detect_lang() -> str:
    v = (_argv_value("--lang") or "").lower()
    if v in LANGS:
        return v
    v = os.environ.get("SME_LANG", "").lower()
    if v in LANGS:
        return v
    try:
        v = str(json.loads(_settings_path().read_text(encoding="utf-8")).get("lang", "")).lower()
        if v in LANGS:
            return v
    except Exception:
        pass
    loc = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    return "uk" if loc.lower().startswith("uk") else "en"

def save_lang(lang: str) -> None:
    p = _settings_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        data = {}
    data["lang"] = lang
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")

_LANG = _detect_lang()

def tr(text: str, *args) -> str:
    s = _EN.get(text, text) if _LANG == "en" else text
    return s.format(*args) if args else s

_EN = {
    "\n  ...і ще {0} пар(и) менш важливих конфліктів — див. conflicts.json / conflicts.tsv":
        "\n  ...and {0} more pair(s) of less important conflicts — see conflicts.json / conflicts.tsv",
    "\n  «{0}» перекриває «{1}»: {2} файл(ів) ({3})":
        "\n  «{0}» overrides «{1}»: {2} file(s) ({3})",
    "\n  Багато плагінів «впали під час завантаження». Під Proton найчастіша причина — у prefix гри":
        "\n  Many plugins \"crashed while loading\". Under Proton the most common cause is that the game prefix has",
    "\n  Однакових файлів у кількох модах (не конфлікт): {0}":
        "\n  Identical files in several mods (not a conflict): {0}",
    "\n=== FOMOD === (SKSE: {0}; відомих плагінів: {1})":
        "\n=== FOMOD === (SKSE: {0}; known plugins: {1})",
    "\n=== SKSE через кнопку «Грати» ===":
        "\n=== SKSE via the Steam \"Play\" button ===",
    "\n=== КОЛЕКЦІЯ: {0} (автор {1}) ===":
        "\n=== COLLECTION: {0} (by {1}) ===",
    "\n=== КОНФЛІКТИ ===":
        "\n=== CONFLICTS ===",
    "\n=== Повернення оригінального лаунчера ===":
        "\n=== Restoring the original launcher ===",
    "\n=== РОЗГОРТАННЯ{0} ===":
        "\n=== DEPLOYMENT{0} ===",
    "\nSKSE: папку гри не знайдено. Вкажи її через --game \"/шлях/до/Skyrim Special Edition\"":
        "\nSKSE: game folder not found. Set it with --game \"/path/to/Skyrim Special Edition\"",
    "\nSRS виявлено: гра на диску {0} → під час запуску SKSE {1}.":
        "\nSRS detected: game on disk {0} → while SKSE runs {1}.",
    "\nГра запущена — закрий її перед відкатом.":
        "\nThe game is running — close it before rolling back.",
    "\nЗвіт збережено:":
        "\nReport saved:",
    "\nЗупинено користувачем.":
        "\nStopped by user.",
    "\nМоди розгорнуто в папку гри. Повний відкат: --undeploy":
        "\nMods deployed to the game folder. Full rollback: --undeploy",
    "\nПапку гри не знайдено. Вкажи її через --game \"/шлях/до/Skyrim Special Edition\"":
        "\nGame folder not found. Set it with --game \"/path/to/Skyrim Special Edition\"",
    "\nПримітка: без --deploy скрипт не змінює папку гри і plugins.txt.":
        "\nNote: without --deploy the script does not change the game folder or plugins.txt.",
    "\nРозгортання не знайдено — нічого відкочувати.":
        "\nNo deployment found — nothing to roll back.",
    "\nУВАГА: лаунчер підмінено (--skse-launcher), а SRS патчить SkyrimSELauncher.exe — поверни його: --skse-launcher-restore":
        "\nWARNING: the launcher is replaced (--skse-launcher), but SRS patches SkyrimSELauncher.exe — restore it: --skse-launcher-restore",
    "\nУВАГА: порядок модів порушує залежності (див. вище) — плагіни можуть не завантажитись.":
        "\nWARNING: the mod order breaks dependencies (see above) — plugins may fail to load.",
    "        авто: {0}":
        "        auto: {0}",
    "        вимагає: {0}":
        "        requires: {0}",
    "      (кращий варіант — в іншій групі-альтернативі цього кроку)":
        "      (the better option is in another alternative group of this step)",
    "      змінити можна через --fomod-redo без --auto":
        "      can be changed with --fomod-redo without --auto",
    "      рівноцінні за залежностями: {0} — взято перший; це питання смаку,":
        "      equal by dependencies: {0} — took the first; it's a matter of taste,",
    "      чому «{0}»: {1}":
        "      why «{0}»: {1}",
    "    -> запусти з --fomod, щоб вибрати варіант (або --fomod --auto)":
        "    -> run with --fomod to choose an option (or --fomod --auto)",
    "    ... повний список у conflicts.tsv":
        "    ... full list in conflicts.tsv",
    "    ...і ще {0}":
        "    ...and {0} more",
    "    з них у BSA «{0}»: {1} — loose-файли перемагають BSA незалежно від порядку":
        "    of them in BSA «{0}»: {1} — loose files beat BSAs regardless of order",
    "   (варіанти, позначені «альтернатива», замінили б уже вибране вище)":
        "   (options marked \"alternative\" would replace the one already chosen above)",
    "   (встановлюються всі)":
        "   (all are installed)",
    "  (Protontricks є в Discover). Це найімовірніша причина, але не гарантована.":
        "  (Protontricks is in Discover). This is the most likely cause, but not guaranteed.",
    "  (автор SRS тестував його з Proton Experimental — якщо SRS не спрацює, обери його)":
        "  (the SRS author tested it with Proton Experimental — if SRS does not work, choose it)",
    "  (альтернатива до «{0}»)":
        "  (alternative to «{0}»)",
    "  (непридатний)":
        "  (not usable)",
    "  --yes задано, але наявну папку не перезаписую; архів пропущено.\n":
        "  --yes given, but an existing folder is not overwritten; archive skipped.\n",
    "  ...повний список у mod_engine_output/collection_report.txt":
        "  ...full list in mod_engine_output/collection_report.txt",
    "  ...і ще {0} (повний список у plugins.txt)":
        "  ...and {0} more (full list in plugins.txt)",
    "  ...і ще {0} повідомлень LOOT — див. mod_engine_output/loot_report.txt":
        "  ...and {0} more LOOT messages — see mod_engine_output/loot_report.txt",
    "  BSA не вдалося прочитати (вміст не враховано): {0}":
        "  Could not read BSA (contents not counted): {0}",
    "  MD5: {0}/{1} архівів...":
        "  MD5: {0}/{1} archives...",
    "  [o] розпакувати в нову папку з суфіксом / [s] пропустити? [s]: ":
        "  [o] extract into a new folder with a suffix / [s] skip? [s]: ",
    "  runtime у лозі: {0}":
        "  runtime in the log: {0}",
    "  {0} (порядок модів)":
        "  {0} (mod order)",
    "  {0} (усі конфліктні файли)":
        "  {0} (all conflicting files)",
    "  {0}/ (списки файлів модів, .tsv.gz)":
        "  {0}/ (mod file lists, .tsv.gz)",
    "  Багато SKSE-плагінів тоді падають («fatal error occurred while loading plugin»). Виправлення:":
        "  Many SKSE plugins then fail (\"fatal error occurred while loading plugin\"). Fix:",
    "  Вибір (номери через пробіл, Enter = {0}{1}): ":
        "  Choice (numbers separated by spaces, Enter = {0}{1}): ",
    "  Вибір не дає жодного файлу — нічого не встановлено.":
        "  The choice installs no files — nothing installed.",
    "  Вкажи номери.":
        "  Enter numbers.",
    "  Вміст: {0} файлів; плагіни: {1}; верхні папки: {2}":
        "  Contents: {0} files; plugins: {1}; top folders: {2}",
    "  Готово: {0} файлів -> {1}":
        "  Done: {0} files -> {1}",
    "  Готово: розпаковано файлів: {0} -> {1}\n":
        "  Done: extracted files: {0} -> {1}\n",
    "  Назва папки: {0}":
        "  Folder name: {0}",
    "  Не вдалося прочитати вміст: {0}\n":
        "  Could not read contents: {0}\n",
    "  Немає такого номера.":
        "  No such number.",
    "  ПОМИЛКА FOMOD: {0}":
        "  FOMOD ERROR: {0}",
    "  ПОМИЛКА встановлення: {0}":
        "  Install ERROR: {0}",
    "  ПОМИЛКА розпакування: {0}\n":
        "  Extraction ERROR: {0}\n",
    "  Папка вже існує: {0}":
        "  Folder already exists: {0}",
    "  Пропущено.\n":
        "  Skipped.\n",
    "  Розпакувати в '{0}'? [Y/n]: ":
        "  Extract to '{0}'? [Y/n]: ",
    "  завантажено: {0}; не завантажено: {1}":
        "  loaded: {0}; not loaded: {1}",
    "  каталог: {0}/{1} модів...":
        "  catalog: {0}/{1} mods...",
    "  немає справжнього Visual C++ runtime (вбудована заміна Wine неповна). Спробуй встановити його:":
        "  no real Visual C++ runtime (Wine's built-in replacement is incomplete). Try installing it:",
    "  розгорнуто {0}/{1} файлів...":
        "  deployed {0}/{1} files...",
    "  увага: {0}":
        "  warning: {0}",
    " (FOMOD ще не встановлено — запусти --fomod)":
        " (FOMOD not installed yet — run --fomod)",
    " (вибір автора колекції)":
        " (collection author's choice)",
    " (пробний прогін, без змін)":
        " (dry run, no changes)",
    " (є збережений вибір)":
        " (saved choice exists)",
    " (і ще {0} мод(ів) від нього залежать)":
        " (and {0} more mod(s) depend on it)",
    " — варіанти: {0}":
        " — options: {0}",
    "# Порядок модів для Skyrim Mod Engine.\n# Один мод на рядок. НИЖЧЕ = ВИЩИЙ ПРІОРИТЕТ: його файли перекривають файли модів вище.\n# Можна переставляти рядки вручну; нові моди скрипт вставляє сам з урахуванням master-залежностей.\n# Рядки з # ігноруються.\n":
        "# Mod order for Skyrim Mod Engine.\n# One mod per line. LOWER = HIGHER PRIORITY: its files override files of mods above.\n# You can reorder lines by hand; the script inserts new mods itself, respecting master dependencies.\n# Lines starting with # are ignored.\n",
    "(немає вводу — беру відповідь за замовчуванням)":
        "(no input — using the default answer)",
    "(немає)":
        "(none)",
    ", 0 = нічого":
        ", 0 = nothing",
    ", змінених після розгортання відкладено {0} -> {1}":
        ", changed after deployment set aside {0} -> {1}",
    ", пропущено суперечливих {0}":
        ", skipped conflicting {0}",
    ", стосуються модів, яких немає: {0}":
        ", refer to missing mods: {0}",
    ", уже відсутніх {0}":
        ", already missing {0}",
    "32-бітний PE — SKSE64 такий не завантажить":
        "32-bit PE — SKSE64 will not load it",
    "; поза колекцією {0} — розставлено LOOT/власними правилами":
        "; outside the collection {0} — placed by LOOT/own rules",
    "<file> вказує не на файл: {0}":
        "<file> does not point to a file: {0}",
    "<folder> вказує не на папку: {0}":
        "<folder> does not point to a folder: {0}",
    "ESL-плагінів {0} > ліміту {1}":
        "ESL plugins {0} > limit {1}",
    "FOMOD без питань: вибір за залежностями, SKSE і рекомендаціями автора.":
        "FOMOD without questions: choice by dependencies, SKSE and author recommendations.",
    "FOMOD позначає як непридатний":
        "FOMOD marks it as not usable",
    "FOMOD-інсталятор: вміст залежить від вибраних опцій":
        "FOMOD installer: contents depend on the chosen options",
    "LOOT вимкнено (--no-loot) — порядок за власними правилами":
        "LOOT disabled (--no-loot) — order by own rules",
    "Proton для гри: {0}":
        "Proton for the game: {0}",
    "Proton для гри: типовий для Steam (не задано окремо)":
        "Proton for the game: Steam default (not set separately)",
    "Proton-prefix гри не знайдено — запусти гру хоча б раз через Steam; plugins.txt не змінено":
        "Game Proton prefix not found — launch the game at least once through Steam; plugins.txt not changed",
    "Proton-prefix гри ще не створено — запусти гру один раз через Steam":
        "Game Proton prefix not created yet — launch the game once through Steam",
    "RVA {0:#x} поза секціями":
        "RVA {0:#x} outside sections",
    "SKSE встановлено":
        "SKSE installed",
    "SKSE зібраний під іншу версію гри ({0}), гра {1}":
        "SKSE built for another game version ({0}), game {1}",
    "SKSE не встановлено":
        "SKSE not installed",
    "SKSE-плагін без структури Data — його місце в Data/SKSE/Plugins":
        "SKSE plugin without Data structure — it belongs in Data/SKSE/Plugins",
    "SKSE: не встановлено в папці гри":
        "SKSE: not installed in the game folder",
    "SKSE: є ({0}); відповідає грі: {1}":
        "SKSE: present ({0}); matches the game: {1}",
    "SRS ({0}): SKSE і плагіни перевіряються під {1}":
        "SRS ({0}): SKSE and plugins are checked against {1}",
    "Skyrim Mod Engine v0.10 — аналіз, розпакування, каталог модів, перевірка SKSE.":
        "Skyrim Mod Engine v0.10 — analysis, extraction, mod catalog, SKSE checks.",
    "Visual C++ runtime у prefix: справжній (Microsoft) і ввімкнений — OK":
        "Visual C++ runtime in the prefix: real (Microsoft) and enabled — OK",
    "Windows не зміг завантажити DLL":
        "Windows could not load the DLL",
    "Windows не зміг завантажити DLL (код {0}: {1})":
        "Windows could not load the DLL (code {0}: {1})",
    "ZIP із методом стиснення, який Python не підтримує, а 7z/bsdtar немає":
        "ZIP with a compression method Python does not support, and no 7z/bsdtar",
    "[{0}/{1}] {2}: уже встановлено ({3}); --fomod-redo щоб перевстановити":
        "[{0}/{1}] {2}: already installed ({3}); --fomod-redo to reinstall",
    "[гра] Data":
        "[game] Data",
    "collection.json колекції Vortex/Nexus: вибори FOMOD автора, порядок модів і плагінів, перевірка архівів. 'none' — відключити.":
        "Vortex/Nexus collection.json: author's FOMOD choices, mod and plugin order, archive check. 'none' — disable.",
    "collection.json не прочитано: {0}":
        "collection.json not read: {0}",
    "collection.json не схожий на колекцію Vortex (немає списку mods)":
        "collection.json does not look like a Vortex collection (no mods list)",
    "fomod/ModuleConfig.xml не знайдено":
        "fomod/ModuleConfig.xml not found",
    "masterlist LOOT {0} з кешу ({1:.0f} дн.)":
        "LOOT masterlist {0} from cache ({1:.0f} days)",
    "masterlist LOOT {0} завантажено":
        "LOOT masterlist {0} downloaded",
    "masterlist LOOT не оновлено (немає зв'язку) — використовую кеш {0} ({1:.0f} дн.)":
        "LOOT masterlist not updated (no connection) — using cache {0} ({1:.0f} days)",
    "masterlist LOOT не прочитано ({0}) — порядок за власними правилами":
        "LOOT masterlist not read ({0}) — order by own rules",
    "masterlist LOOT недоступний (немає інтернету й кешу) — порядок за власними правилами":
        "LOOT masterlist unavailable (no internet and no cache) — order by own rules",
    "skse64_loader.exe не знайдено в папці гри":
        "skse64_loader.exe not found in the game folder",
    "skse64_loader.exe немає в папці гри. Спершу --deploy (мод SKSE) або встанови SKSE вручну.":
        "skse64_loader.exe is not in the game folder. First --deploy (SKSE mod) or install SKSE manually.",
    "{0}: «брудний» ({1}) — почисти в SSEEdit":
        "{0}: \"dirty\" ({1}) — clean it in SSEEdit",
    "{0}: НЕ активовано — немає master {1} (гра впала б на старті)":
        "{0}: NOT activated — master {1} is missing (the game would crash at startup)",
    "{0}: немає master {1}":
        "{0}: master {1} is missing",
    "{0}: несумісний з {1}":
        "{0}: incompatible with {1}",
    "{0}: потрібен {1}":
        "{0}: requires {1}",
    "«{0}» непридатний: {1}":
        "«{0}» is not usable: {1}",
    "«{0}» ставить ті самі файли, що вже вибраний «{1}» ({2}) — це альтернативи, встановився б лише один. Залиш цю групу порожньою (просто Enter) або почни заново з --fomod-redo":
        "«{0}» installs the same files as the already chosen «{1}» ({2}) — these are alternatives, only one would be installed. Leave this group empty (just Enter) or start over with --fomod-redo",
    "«{0}» стоїть вище за «{1}», від якого залежать його плагіни — перенеси «{2}» вище в modlist.txt":
        "«{0}» is above «{1}», which its plugins depend on — move «{2}» up in modlist.txt",
    "Є плагіни, яким потрібна Address Library, але {0} не знайдено ні в грі, ні в модах":
        "Some plugins need Address Library, but {0} was found neither in the game nor in mods",
    "Інструменти для 7z/RAR/TAR: {0}":
        "Tools for 7z/RAR/TAR: {0}",
    "Автор SRS тестував це з Proton Experimental — краще обрати саме його в «Сумісність».":
        "The SRS author tested this with Proton Experimental — better choose exactly that under \"Compatibility\".",
    "Архів містить символьні посилання, моди їх не потребують: ":
        "The archive contains symbolic links, mods do not need them: ",
    "Архіви, яких немає в колекції ({0}) — інші версії чи зайві файли:":
        "Archives not in the collection ({0}) — other versions or extra files:",
    "Архіви: {0}":
        "Archives: {0}",
    "Архівів знайдено: обов'язкових {0}/{1}, необов'язкових {2}/{3}":
        "Archives found: required {0}/{1}, optional {2}/{3}",
    "ВІДСУТНІ ОБОВ'ЯЗКОВІ":
        "MISSING REQUIRED",
    "Версія гри {0}: збігається з колекцією ({1})":
        "Game version {0}: matches the collection ({1})",
    "Версія гри: {0}":
        "Game version: {0}",
    "Встановити FOMOD-моди (вибір варіантів) у папки «<мод> [FOMOD]».":
        "Install FOMOD mods (choose options) into folders \"<mod> [FOMOD]\".",
    "Відкат: видалено розгорнутих файлів {0}, відновлено оригіналів {1}":
        "Rollback: removed deployed files {0}, restored originals {1}",
    "Готово: {0} файлів ({1} жорстких посилань). Відкат: --undeploy":
        "Done: {0} files ({1} hard links). Rollback: --undeploy",
    "Готово: кнопка «Грати» в Steam тепер запускає SKSE у рідному prefix гри.":
        "Done: the Steam \"Play\" button now starts SKSE in the game's own prefix.",
    "Гра (або SKSE) зараз запущена — закрий її перед розгортанням.":
        "The game (or SKSE) is running — close it before deploying.",
    "Гра запущена — закрий її спершу.":
        "The game is running — close it first.",
    "Гра: {0}":
        "Game: {0}",
    "Для цього формату потрібна утиліта 7z, bsdtar, unar або unrar.":
        "This format needs 7z, bsdtar, unar or unrar.",
    "З --deploy: показати план без змін.":
        "With --deploy: show the plan without changes.",
    "Знайдено unar, але немає lsar для перегляду вмісту.":
        "Found unar, but no lsar to list contents.",
    "Знайдено архівів: {0}\n":
        "Archives found: {0}\n",
    "КРИТИЧНО: плагін {0} є одразу в: {1} — у грі буде лише один, решта перекриті":
        "CRITICAL: plugin {0} is in several mods at once: {1} — only one will be in the game, the rest are overridden",
    "Кнопка «Грати» в Steam запускатиме SKSE (підміна SkyrimSELauncher.exe, з бекапом).":
        "The Steam \"Play\" button will start SKSE (replaces SkyrimSELauncher.exe, with a backup).",
    "Колекцію відключено.":
        "Collection disabled.",
    "Колекція: {0}":
        "Collection: {0}",
    "Конфліктів файлів немає.":
        "No file conflicts.",
    "Кілька альтернативних папок, схожих на Data — потрібен вибір":
        "Several alternative folders that look like Data — a choice is needed",
    "Лаунчер SKSE прибрано (оригіналу не було).":
        "SKSE launcher removed (there was no original).",
    "Лаунчер не змінювався — нічого повертати.":
        "The launcher was not changed — nothing to restore.",
    "Лаунчер не підмінюй (--skse-launcher): SRS сам патчить SkyrimSELauncher.exe.":
        "Do not replace the launcher (--skse-launcher): SRS patches SkyrimSELauncher.exe itself.",
    "Лаунчер уже не наш (Steam, мабуть, відновив оригінал) — залишаю як є.":
        "The launcher is no longer ours (Steam probably restored the original) — leaving it as is.",
    "Лише проаналізувати нові архіви, не розпаковувати.":
        "Only analyze new archives, do not extract.",
    "Лог skse64.log не знайдено (гра ще не запускалась через SKSE?)":
        "skse64.log not found (the game has not been started through SKSE yet?)",
    "Лог останнього запуску: {0}":
        "Log of the last launch: {0}",
    "Моди в каталозі: {0}":
        "Mods in catalog: {0}",
    "Моди: {0}":
        "Mods: {0}",
    "Модів: {0}; файлів у Data: {1}; файлів у папку гри: {2}":
        "Mods: {0}; files in Data: {1}; files to the game folder: {2}",
    "НІ":
        "NO",
    "НІ ":
        "NO ",
    "Наявних файлів гри перекрито (збережено в mod_engine_output/backup): {0}":
        "Existing game files overridden (saved in mod_engine_output/backup): {0}",
    "Не використовувати masterlist LOOT для порядку плагінів.":
        "Do not use the LOOT masterlist for plugin order.",
    "Не враховано: {0}":
        "Not counted: {0}",
    "Не підмінюю лаунчер: у збірці є Skyrim Runtime Swapper, а він сам патчить SkyrimSELauncher.exe.":
        "Not replacing the launcher: the setup has Skyrim Runtime Swapper, which patches SkyrimSELauncher.exe itself.",
    "Немає {0} — цей SKSE не для гри {1}. Лаунчер не змінено.":
        "{0} is missing — this SKSE is not for game {1}. Launcher not changed.",
    "Немає утиліти для цього формату.":
        "No tool for this format.",
    "Окремий ярлик на skse64_loader.exe у Steam більше не потрібен (його prefix можна видалити разом з ярликом).":
        "A separate Steam shortcut to skse64_loader.exe is no longer needed (its prefix can be deleted along with the shortcut).",
    "Оригінальний SkyrimSELauncher.exe повернено.":
        "The original SkyrimSELauncher.exe is restored.",
    "Оригінальний лаунчер збережено; повернути: --skse-launcher-restore":
        "The original launcher is saved; to restore: --skse-launcher-restore",
    "ПОМИЛКА розгортання: {0}":
        "Deployment ERROR: {0}",
    "Папка гри (за замовч. шукається в бібліотеках Steam).":
        "Game folder (searched in Steam libraries by default).",
    "Папка для звітів (за замовч. ./mod_engine_output)":
        "Folder for reports (default ./mod_engine_output)",
    "Папка з архівами (за замовч. ./mods-archives)":
        "Folder with archives (default ./mods-archives)",
    "Папка з розпакованими модами (за замовч. ./Mods)":
        "Folder with extracted mods (default ./Mods)",
    "Папку «{0}» видалено — розпакую {1} знову":
        "Folder «{0}» was deleted — extracting {1} again",
    "Параметри запуску Steam (акаунт {0}): SRS і SKSE налаштовані — OK":
        "Steam launch options (account {0}): SRS and SKSE configured — OK",
    "Параметри запуску Steam (акаунт {0}): {1}":
        "Steam launch options (account {0}): {1}",
    "Перевстановити вже встановлені FOMOD (з урахуванням збереженого вибору).":
        "Reinstall already installed FOMODs (reusing saved choices).",
    "Перевірити SKSE, Address Library і SKSE-плагіни модів.":
        "Check SKSE, Address Library and the mods' SKSE plugins.",
    "Перейменовано: '{0}' -> '{1}'":
        "Renamed: '{0}' -> '{1}'",
    "Плагіни (статичний прогноз):":
        "Plugins (static forecast):",
    "План записано в журнал; запусти --undeploy, щоб повністю відкотити.":
        "The plan is written to the journal; run --undeploy to roll back completely.",
    "Повернути оригінальний SkyrimSELauncher.exe.":
        "Restore the original SkyrimSELauncher.exe.",
    "Повністю відкотити розгортання: прибрати файли модів, відновити оригінали і plugins.txt.":
        "Fully roll back the deployment: remove mod files, restore originals and plugins.txt.",
    "Показати конфлікти файлів між модами з урахуванням порядку (modlist.txt).":
        "Show file conflicts between mods, taking the order into account (modlist.txt).",
    "Поруч з {0} є інші файли — перевір вручну":
        "There are other files next to {0} — check manually",
    "Порядок модів (нижче = перемагає; редагується в mod_engine_output/modlist.txt):":
        "Mod order (lower = wins; edit in mod_engine_output/modlist.txt):",
    "Порядок плагінів ({0}):":
        "Plugin order ({0}):",
    "Прибираю попереднє розгортання...":
        "Removing the previous deployment...",
    "Примусово оновити masterlist LOOT (інакше раз на 7 днів).":
        "Force a LOOT masterlist update (otherwise once every 7 days).",
    "Розгорнути моди в папку гри (жорсткі посилання, з бекапом) і оновити plugins.txt.":
        "Deploy mods into the game folder (hard links, with backup) and update plugins.txt.",
    "Розпаковувати нові архіви без питань.":
        "Extract new archives without asking.",
    "У {0} немає папки Data — розгортання скасовано.":
        "{0} has no Data folder — deployment cancelled.",
    "УВАГА (порядок модів): {0}":
        "WARNING (mod order): {0}",
    "УВАГА: DLL Visual C++ від Microsoft є, але Wine їх не використовує (немає DllOverrides для ":
        "WARNING: Microsoft Visual C++ DLLs are present, but Wine does not use them (no DllOverrides for ",
    "УВАГА: {0}":
        "WARNING: {0}",
    "УВАГА: колекція зібрана під {0}, а гра {1}":
        "WARNING: the collection was built for {0}, but the game is {1}",
    "УВАГА: параметри запуску (акаунт {0}) без WINEDLLOVERRIDES=\"version=n,b\" — SRS може не спрацювати":
        "WARNING: launch options (account {0}) lack WINEDLLOVERRIDES=\"version=n,b\" — SRS may not work",
    "УВАГА: параметри запуску (акаунт {0}) не запускають skse64_loader.exe — гра стартує без SKSE":
        "WARNING: launch options (account {0}) do not start skse64_loader.exe — the game starts without SKSE",
    "УВАГА: у Steam (акаунт {0}) немає параметрів запуску для SRS/SKSE. Встав у Властивості → Параметри запуску:":
        "WARNING: Steam (account {0}) has no launch options for SRS/SKSE. Put into Properties → Launch Options:",
    "УВАГА: у prefix гри немає справжнього Visual C++ runtime: ":
        "WARNING: the game prefix has no real Visual C++ runtime: ",
    "Увага: «Грати» досі запускає SKSE (лаунчер підмінено). Якщо SKSE був із мода і його прибрано, гра не стартує — поверни лаунчер: --skse-launcher-restore":
        "Warning: \"Play\" still starts SKSE (the launcher is replaced). If SKSE came from a mod and was removed, the game will not start — restore the launcher: --skse-launcher-restore",
    "Уже встановлено: «Грати» в Steam запускає SKSE.":
        "Already set: \"Play\" in Steam starts SKSE.",
    "Уже розпаковані раніше (без змін): {0}\n":
        "Already extracted earlier (unchanged): {0}\n",
    "Файл колекції не знайдено: {0}":
        "Collection file not found: {0}",
    "Шлях до skse64.log (за замовч. шукається в compatdata Proton).":
        "Path to skse64.log (searched in Proton compatdata by default).",
    "Щоб SRS спрацював під Proton, а SKSE стартував з кнопки «Грати» у рідному prefix гри,":
        "For SRS to work under Proton and SKSE to start from the \"Play\" button in the game's own prefix,",
    "авто":
        "auto",
    "автор колекції обрав «{0}», хоча: {1}":
        "the collection author chose «{0}», although: {1}",
    "автор позначив як несумісні: «{0}» і «{1}»":
        "the author marked as incompatible: «{0}» and «{1}»",
    "активних плагінів":
        "active plugins",
    "активних плагінів: звичайних {0}/{1}, ESL {2}/{3} (з офіційними та CC)":
        "active plugins: regular {0}/{1}, ESL {2}/{3} (incl. official and CC)",
    "без runtime DLL":
        "no runtime DLL",
    "бракує DLL, від якої він залежить":
        "a DLL it depends on is missing",
    "бракує функції в іншій DLL":
        "a function in another DLL is missing",
    "будь-які":
        "any",
    "в описі вимагає «{0}» — схожого мода в тебе не знайдено":
        "the description requires «{0}» — no similar mod found in your setup",
    "важливо":
        "important",
    "варіант без SKSE, а SKSE встановлено":
        "option without SKSE, but SKSE is installed",
    "вибір автора для «{0}» не зіставився з інсталятором — взято авто-вибір":
        "the author's choice for «{0}» did not match the installer — took the auto choice",
    "вибір автора колекції":
        "collection author's choice",
    "використовує структури до 1.6.629":
        "uses pre-1.6.629 structures",
    "вимога з опису «{0}» є: {1}":
        "requirement from description «{0}» is present: {1}",
    "впав під час завантаження":
        "crashed while loading",
    "встав у Steam → Skyrim → Властивості → Параметри запуску цей рядок:":
        "put this line into Steam → Skyrim → Properties → Launch Options:",
    "відсутні необов'язкові":
        "missing optional",
    "відсутній":
        "missing",
    "джерело не знайдено: {0}":
        "source not found: {0}",
    "для «{0} / {1}» у колекції немає вибору — взято авто-вибір":
        "the collection has no choice for «{0} / {1}» — took the auto choice",
    "додано в порядок: {0}":
        "added to the order: {0}",
    "жодної":
        "none",
    "заглушка Wine":
        "Wine stub",
    "залежить від {0}, який у тебе є":
        "depends on {0}, which you have",
    "збережений {0}-вибір":
        "saved {0} choice",
    "збережений вибір для «{0}» більше не підходить до поточного набору модів — взято авто-вибір":
        "the saved choice for «{0}» no longer fits the current set of mods — took the auto choice",
    "звичайних плагінів {0} > ліміту {1} — гра не запуститься/впаде. Вимкни частину або заміни на ESL-версії (ESL рахуються окремо, до {2})":
        "regular plugins {0} > limit {1} — the game will not start/will crash. Disable some or switch to ESL versions (ESL plugins count separately, up to {2})",
    "зібраний під {0}, гра {1}":
        "built for {0}, game {1}",
    "зібраний під іншу версію гри":
        "built for another game version",
    "косметично":
        "cosmetic",
    "критично":
        "critical",
    "лише SKSEPlugin_Version — плагін тільки для AE (1.6+)":
        "only SKSEPlugin_Version — AE-only plugin (1.6+)",
    "лише в Mods — ще не розгорнуто":
        "only in Mods — not deployed yet",
    "має SKSEPlugin_Query (остаточно версію перевіряє сам плагін під час запуску)":
        "has SKSEPlugin_Query (the plugin itself checks the version at startup)",
    "можна вибрати не більше одного":
        "at most one may be chosen",
    "містить SKSE-плагін, а SKSE не встановлено":
        "contains an SKSE plugin, but SKSE is not installed",
    "не 64-бітна або пошкоджена DLL":
        "not a 64-bit DLL or damaged",
    "не PE-файл (немає MZ)":
        "not a PE file (no MZ)",
    "не PE-файл (немає PE)":
        "not a PE file (no PE)",
    "не SKSE-плагін або плагін для старої SE 1.5.97":
        "not an SKSE plugin or a plugin for old SE 1.5.97",
    "не більше одного":
        "at most one",
    "не вдалося визначити":
        "could not determine",
    "не знайдено":
        "not found",
    "небезпечний шлях: {0}":
        "unsafe path: {0}",
    "невідома помилка":
        "unknown error",
    "невідомий якір *{0}":
        "unknown anchor *{0}",
    "незакрита [":
        "unclosed [",
    "незакрита [ з рядка {0}":
        "unclosed [ from line {0}",
    "незакриті лапки з рядка {0}":
        "unclosed quotes from line {0}",
    "незалежний від версії (Address Library/сигнатури)":
        "version-independent (Address Library/signatures)",
    "немає":
        "missing",
    "немає SKSEPlugin_Version — плагін для старої SE 1.5.97":
        "no SKSEPlugin_Version — plugin for old SE 1.5.97",
    "немає експортів SKSEPlugin_* — допоміжна DLL або не SKSE":
        "no SKSEPlugin_* exports — helper DLL or not SKSE",
    "неочікуваний відступ у рядку {0}":
        "unexpected indent in line {0}",
    "нічого":
        "nothing",
    "обов'язковий у FOMOD":
        "required in FOMOD",
    "очікувався ключ у рядку {0}: {1}":
        "expected a key in line {0}: {1}",
    "перекрито копією з «{0}»":
        "overridden by the copy from «{0}»",
    "плагінів із групою LOOT: {0}":
        "plugins with a LOOT group: {0}",
    "помилка доступу до пам'яті під час завантаження DLL":
        "memory access error while loading the DLL",
    "помірно":
        "moderate",
    "порядок плагінів автора колекції: {0} з {1} плагінів":
        "collection author's plugin order: {0} of {1} plugins",
    "потребує SKSE, а його немає":
        "needs SKSE, but it is missing",
    "правил LOOT «після»: застосовано {0}":
        "LOOT \"after\" rules: applied {0}",
    "правил порядку модів з колекції: застосовано {0}":
        "collection mod order rules: applied {0}",
    "прибрано з порядку модів, яких більше немає: {0}":
        "removed from the mod order, no longer present: {0}",
    "рекомендований автором":
        "recommended by the author",
    "розгорнуто":
        "deployed",
    "розпаковано через {0} (нестандартне стиснення ZIP)":
        "extracted with {0} (non-standard ZIP compression)",
    "ручний":
        "manual",
    "рівно один":
        "exactly one",
    "сам відмовився працювати (несумісна версія гри чи залежності)":
        "refused to run itself (incompatible game version or dependencies)",
    "сам відмовився працювати (несумісна версія гри)":
        "refused to run itself (incompatible game version)",
    "так":
        "yes",
    "треба вибрати рівно один":
        "exactly one must be chosen",
    "треба вибрати хоча б один":
        "at least one must be chosen",
    "умови moduleDependencies не виконані (бракує потрібних модів?)":
        "moduleDependencies conditions not met (missing required mods?)",
    "усі":
        "all",
    "файл\tрівень\tперемагає\tперекриті\n":
        "file\tlevel\twins\toverridden\n",
    "хоча б один":
        "at least one",
    "явно підтримує {0}":
        "explicitly supports {0}",
    "є":
        "present",
    "# «-» на початку рядка (-Назва моду) — мод вимкнено в цьому профілі; приберіть «-», щоб увімкнути.\n":
        "# A \"-\" at the start of a line (-Mod name) disables the mod in this profile; remove the \"-\" to enable it.\n",
    "Створено профіль «{0}» (копія «{1}»).":
        "Created profile «{0}» (a copy of «{1}»).",
    "\nПрофілі:":
        "\nProfiles:",
    "Порядок модів (нижче = перемагає; редагується в modlist.txt профілю):":
        "Mod order (lower = wins; edit in the profile's modlist.txt):",
    "Профіль: {0}":
        "Profile: {0}",
    "Недопустима назва профілю: «{0}» (літери, цифри, пробіл, . ( ) - _; до 40 символів)":
        "Invalid profile name: «{0}» (letters, digits, space, . ( ) - _; up to 40 characters)",
    "Профіль «{0}» уже існує.":
        "Profile «{0}» already exists.",
    "Профілю «{0}» немає.":
        "There is no profile «{0}».",
    "Не можу видалити активний профіль «{0}» — спершу перемкнись на інший (--profile).":
        "Cannot delete the active profile «{0}» — switch to another one first (--profile).",
    "Профіль «{0}» зараз розгорнуто — спершу --undeploy або розгорни інший.":
        "Profile «{0}» is deployed right now — run --undeploy or deploy another one first.",
    "Профіль «{0}» видалено.":
        "Profile «{0}» deleted.",
    "Файли профілю (overwrite): {0}":
        "Profile files (overwrite): {0}",
    "УВАГА: моди й гра на різних файлових системах — {0} файл(ів) ({1}) буде скопійовано, а не злінковано (займе місце на диску гри).":
        "WARNING: mods and the game are on different filesystems — {0} file(s) ({1}) will be copied instead of linked (uses space on the game drive).",
    "Профіль, з яким працювати (запам'ятовується). За замовч. — останній вибраний, спершу Default.":
        "Profile to work with (remembered). Default: the last one chosen, initially Default.",
    "Створити профіль-копію поточного і перемкнутися на нього.":
        "Create a profile as a copy of the current one and switch to it.",
    "Видалити профіль (не активний і не розгорнутий).":
        "Delete a profile (not the active or deployed one).",
    "Показати список профілів.":
        "List profiles.",
    "  (у грі зараз розгорнуто профіль «{0}»; щоб перемкнути гру — --deploy)":
        "  (profile «{0}» is deployed in the game now; to switch the game — --deploy)",
    "Вимкнено в профілі: {0} мод(ів)":
        "Disabled in profile: {0} mod(s)",
    "  Такий самий вибір уже встановлено для профілю «{0}» — використовую {1}":
        "  The same choice is already installed for profile «{0}» — using {1}",
    " Прибрано FOMOD-варіантів, які більше ніхто не використовує: {0}":
        " Removed FOMOD variants no longer used by anyone: {0}",
    "Прибираю попереднє розгортання (профіль «{0}»)...":
        "Removing the previous deployment (profile «{0}»)...",
    "  збережено в overwrite профілю «{0}»: {1} файл(ів), змінених чи створених грою":
        "  saved into the overwrite of profile «{0}»: {1} file(s) changed or created by the game",
    "Недостатньо місця на диску гри: потрібно {0}, вільно {1}. Розгортання скасовано.":
        "Not enough space on the game drive: {0} needed, {1} free. Deployment cancelled.",
    "Конфіги, які гра може змінювати, скопійовано окремо: {0} (зміни збережуться в профілі, а не в Mods/)":
        "Configs the game may change were copied separately: {0} (changes are kept in the profile, not in Mods/)",
    "Профілю «{0}» немає. Є: {1}. Створити: --profile-new \"{0}\"":
        "There is no profile «{0}». Existing: {1}. To create: --profile-new \"{0}\"",
    "активний":
        "active",
    "«{0}» залежить від вимкненого «{1}» — увімкни його або вимкни «{2}»":
        "«{0}» depends on the disabled «{1}» — enable it or disable «{2}»",
    ", змінених чи створених грою збережено в профіль «{0}» (overwrite): {1}":
        ", changed or created by the game saved into profile «{0}» (overwrite): {1}",
    "  {0} використовує також профіль(і) {1} — новий вибір встановлюю в {2}":
        "  {0} is also used by profile(s) {1} — installing the new choice into {2}",
    "Запуск гри через Steam...":
        "Starting the game through Steam...",
    "Графічний інтерфейс: {0}":
        "Graphical interface: {0}",
    "Не вдалося відкрити браузер. Відкрий цю адресу вручну:\n{0}":
        "Could not open a browser. Open this address manually:\n{0}",
    "Відкрити графічний інтерфейс у браузері (також: подвійний клік по скрипту).":
        "Open the graphical interface in the browser (also: double-click the script).",

    "Вхід у Nexus: {0} ({1}). Ключ збережено в {2}":
        "Nexus login: {0} ({1}). Key saved in {2}",
    "Nexus: {0} ({1})":
        "Nexus: {0} ({1})",
    "  Завантаження {0} ({1}) з {2}...":
        "  Downloading {0} ({1}) from {2}...",
    "  Готово: {0}":
        "  Done: {0}",
    "Обробник nxm:// зареєстровано: {0}":
        "nxm:// handler registered: {0}",
    "  На сайті Nexus натискай «Mod Manager Download»: файл ляже в mods-archives, потім «Розпакувати нові».":
        "  On the Nexus site press “Mod Manager Download”: the file lands in mods-archives, then “Extract new”.",
    "\n=== Завантаження архівів колекції: {0} ===":
        "\n=== Downloading collection archives: {0} ===",
    "Завантажено: {0}, з помилками: {1}":
        "Downloaded: {0}, with errors: {1}",
    "Персональний API-ключ: nexusmods.com → аватар → Site preferences → API Keys → Personal API key":
        "Personal API key: nexusmods.com → avatar → Site preferences → API Keys → Personal API key",
    "Без Premium Nexus дозволяє завантаження лише кнопкою «Mod Manager Download» на сайті (посилання nxm://). Зареєструй обробник: --nxm-handler":
        "Without Premium, Nexus allows downloads only through the “Mod Manager Download” button on the site (nxm:// links). Register the handler: --nxm-handler",
    "Ключ Nexus видалено з цього комп'ютера.":
        "Nexus key removed from this computer.",
    "Вхід у Nexus не виконано.":
        "Not logged in to Nexus.",
    "Nexus: вхід не виконано (--nexus-login).":
        "Nexus: not logged in (--nexus-login).",
    "Не схоже на посилання nxm:// на файл мода: {0}":
        "Does not look like an nxm:// link to a mod file: {0}",
    "Nexus повернув порожню назву файлу":
        "Nexus returned an empty file name",
    "У мода {0} немає основних файлів для завантаження":
        "Mod {0} has no main files to download",
    "У мода {0} кілька основних файлів, беру «{1}». Інші: {2}":
        "Mod {0} has several main files, taking «{1}». Others: {2}",
    "Посилання для гри «{0}», а скрипт працює з {1}":
        "The link is for game «{0}», but the script works with {1}",
    "  {0}: уже є в mods-archives":
        "  {0}: already in mods-archives",
    "Nexus не дав посилання на завантаження":
        "Nexus gave no download link",
    "    з'єднання обірвалось на {0} — докачую":
        "    connection dropped at {0} — resuming",
    "Розмір не збігається: {0} замість {1}; повтори завантаження":
        "Size mismatch: {0} instead of {1}; repeat the download",
    "MD5 не збігається з очікуваним — файл видалено":
        "MD5 does not match the expected one — file deleted",
    "Завантажено: {0}":
        "Downloaded: {0}",
    "  До цього посилання nxm:// відкривав {0} — тепер їх обробляє цей скрипт.":
        "  nxm:// links were opened by {0} before — now this script handles them.",
    "Спершу увійди в Nexus: --nexus-login":
        "Log in to Nexus first: --nexus-login",
    "Пряме завантаження доступне лише з Nexus Premium. Без нього відкривай посилання з collection_report.txt і натискай «Mod Manager Download» (потрібен --nxm-handler).":
        "Direct downloads need Nexus Premium. Without it, open the links from collection_report.txt and press “Mod Manager Download” (needs --nxm-handler).",
    "Увійти в Nexus Mods персональним API-ключем (без KEY — запитає).":
        "Log in to Nexus Mods with a personal API key (without KEY it asks).",
    "Видалити збережений ключ Nexus.":
        "Remove the saved Nexus key.",
    "Показати, чи виконано вхід у Nexus.":
        "Show whether you are logged in to Nexus.",
    "Завантажити файл мода з Nexus (лише Premium): ID мода, ID:ID_файлу або посилання на сторінку.":
        "Download a mod file from Nexus (Premium only): mod ID, ID:file ID or the page link.",
    "Обробити посилання nxm:// («Mod Manager Download» на сайті).":
        "Handle an nxm:// link (“Mod Manager Download” on the site).",
    "Зареєструвати скрипт як обробник посилань nxm:// у системі.":
        "Register the script as the system handler for nxm:// links.",
    "Завантажити відсутні архіви колекції з Nexus (лише Premium).":
        "Download missing collection archives from Nexus (Premium only).",
    "З --collection-download: також необов'язкові моди колекції.":
        "With --collection-download: optional collection mods too.",
    "Nexus: помилка {0}: {1}":
        "Nexus: error {0}: {1}",
    "Немає з'єднання з Nexus: {0}":
        "No connection to Nexus: {0}",
    "Вкажи ключ: --nexus-login КЛЮЧ (Nexus → Site preferences → API Keys → Personal API key)":
        "Give the key: --nexus-login KEY (Nexus → Site preferences → API Keys → Personal API key)",
    "ПОМИЛКА входу: {0}":
        "Login ERROR: {0}",
    "Premium":
        "Premium",
    "безкоштовний акаунт":
        "free account",
    "Посилання на колекцію (nxm://…/collections/…) поки не підтримується — завантаж collection.json і використай --collection":
        "Collection links (nxm://…/collections/…) are not supported yet — download collection.json and use --collection",
    "спершу увійди в Nexus: --nexus-login":
        "log in to Nexus first: --nexus-login",
    "Nexus: {0}":
        "Nexus: {0}",
    "[{0}/{1}] {2}: немає modId/fileId (не з Nexus) — пропущено":
        "[{0}/{1}] {2}: no modId/fileId (not from Nexus) — skipped",
    "Колекцію не задано: --collection шлях/до/collection.json":
        "No collection set: --collection path/to/collection.json",
    "Nexus не прийняв API-ключ (недійсний або відкликаний). Увійди знову: --nexus-login":
        "Nexus did not accept the API key (invalid or revoked). Log in again: --nexus-login",
    "Nexus відмовив у доступі: {0}":
        "Nexus refused access: {0}",
    "Nexus: не знайдено ({0})":
        "Nexus: not found ({0})",
    "Nexus: вичерпано ліміт запитів API, спробуй пізніше (скидання: {0})":
        "Nexus: API request limit reached, try later (reset: {0})",
    "Встав ключ (не відображається): ":
        "Paste the key (hidden): ",
    "  ПОМИЛКА: {0}":
        "  ERROR: {0}",
    "Не розібрав «{0}»: потрібен ID мода, ID:ID_файлу або посилання на сторінку мода":
        "Could not parse «{0}»: need a mod ID, ID:file ID or the mod page link",
    "Завантаження перервано ({0}); повтори — докачається з місця зупинки":
        "Download interrupted ({0}); repeat — it resumes where it stopped",
    "ПОМИЛКА: {0}":
        "ERROR: {0}",
    "    {0} / {1} ({2}/с)":
        "    {0} / {1} ({2}/s)",
    "Без Premium завантажуй кнопкою «Mod Manager Download» на сайті (потрібен --nxm-handler).":
        "Without Premium, download with the “Mod Manager Download” button on the site (needs --nxm-handler).",
    "Не схоже на посилання на колекцію Nexus: {0}":
        "Does not look like a Nexus collection link: {0}",
    "Nexus не відповів на запит колекції «{0}»: {1}":
        "Nexus did not answer the request for collection «{0}»: {1}",
    "Колекцію збережено: {0} модів (обов'язкових {1}). Вона використовуватиметься в наступних запусках.":
        "Collection saved: {0} mods ({1} required). It will be used in the next runs.",
    "\n=== SKSE для гри {0} (потрібен {1}) ===":
        "\n=== SKSE for game {0} (needs {1}) ===",
    "Не знайшов на Nexus SKSE для гри {0}. Можливо, SKSE ще не оновили під цю версію — тоді краще відкотити гру або взяти SRS.":
        "No SKSE for game {0} found on Nexus. SKSE may not be updated for this version yet — then roll the game back or use SRS.",
    "Колекцію «{0}» не знайдено (або вона прихована чи видалена)":
        "Collection «{0}» not found (or it is hidden or deleted)",
    "Колекція «{0}», ревізія {1}: завантажую файл колекції...":
        "Collection «{0}», revision {1}: downloading the collection file...",
    "Без Premium архіви качаються кнопкою «Mod Manager Download»: список посилань — у collection_report.txt і в GUI (сторінка «Колекція»).":
        "Without Premium, archives are downloaded with the “Mod Manager Download” button: the links are in collection_report.txt and in the GUI (“Collection” page).",
    "Не вдалося визначити версію гри (SkyrimSE.exe).":
        "Could not determine the game version (SkyrimSE.exe).",
    "Потрібний SKSE уже є: {0}":
        "The right SKSE is already present: {0}",
    "На сторінці SKSE на Nexus немає файлів для завантаження.":
        "The SKSE page on Nexus has no files to download.",
    "Найімовірніше потрібний файл: «{0}» (версія {1}).":
        "Most likely the right file: «{0}» (version {1}).",
    "Без Premium: відкрий посилання і натисни «Mod Manager Download» (потрібен --nxm-handler):":
        "Without Premium: open the link and press “Mod Manager Download” (needs --nxm-handler):",
    "  {0} не містить {1} — це не та збірка, видаляю":
        "  {0} does not contain {1} — wrong build, deleting",
    "Взяти колекцію прямо з Nexus: посилання на сторінку колекції, nxm:// або її код.":
        "Get a collection straight from Nexus: collection page link, nxm:// or its code.",
    "Завантажити з Nexus SKSE, що підходить до версії гри.":
        "Download from Nexus the SKSE that matches the game version.",
    "Колекцію збережено: {0} модів. Відкрий інтерфейс або запусти --yes, щоб побачити, яких архівів бракує.":
        "Collection saved: {0} mods. Open the interface or run --yes to see which archives are missing.",
    "Колекція для гри «{0}», а скрипт працює з {1}":
        "The collection is for game «{0}», but the script works with {1}",
    "Nexus не дав посилання на файл колекції":
        "Nexus gave no link to the collection file",
    "У файлі колекції немає collection.json":
        "The collection file has no collection.json",
    "SKSE «{0}» підходить до гри {1}. Розпакуй (--yes) і розгорни (--deploy); старий SKSE у modlist.txt краще вимкнути «-».":
        "SKSE «{0}» matches game {1}. Extract (--yes) and deploy (--deploy); better disable the old SKSE in modlist.txt with “-”.",
    "колекцію не отримано (див. {0})":
        "collection not received (see {0})",
    "УВАГА: ревізію {0} отримати не вдалося, беру останню опубліковану ({1})":
        "WARNING: could not get revision {0}, taking the latest published one ({1})",
    "Немає 7z/bsdtar, щоб розпакувати файл колекції":
        "No 7z/bsdtar to unpack the collection file",
    "у папці гри":
        "in the game folder",
    "\n=== Бекап гри «{0}» ({1}) ===":
        "\n=== Game backup «{0}» ({1}) ===",
    "Гра {0}: {1} файлів, {2}":
        "Game {0}: {1} files, {2}",
    "Бекап готовий: {0} (налаштувань із prefix: {1}{2})":
        "Backup ready: {0} (prefix settings: {1}{2})",
    "\n=== Відновлення гри з бекапу «{0}» ({1}) ===":
        "\n=== Restoring the game from backup «{0}» ({1}) ===",
    "Гру відновлено: файлів повернено {0}, зайвих відкладено {1}, налаштувань prefix {2}.":
        "Game restored: files returned {0}, extra set aside {1}, prefix settings {2}.",
    "  Щоб Steam не оновив гру знову: Властивості → Оновлення — вибери оновлення лише під час запуску, а запускай через SKSE.":
        "  To keep Steam from updating the game again: Properties → Updates — choose updating only when launched, and start through SKSE.",
    "Бекап «{0}» видалено.":
        "Backup «{0}» deleted.",
    "Зараз розгорнуто моди — це вже не чиста гра. Спершу --undeploy, потім бекап, потім знову --deploy.":
        "Mods are deployed right now — this is not a clean game. First --undeploy, then back up, then --deploy again.",
    "Недопустима назва бекапу: «{0}»":
        "Invalid backup name: «{0}»",
    "Бекап «{0}» уже існує.":
        "Backup «{0}» already exists.",
    "УВАГА: схоже, гра вже не зовсім чиста (це потрапить у бекап): {0}":
        "WARNING: the game does not look fully clean (this goes into the backup): {0}",
    "  Режим link: місця не займає, але якщо Steam змінить файл на місці, змінюється й бекап — перевірка: --backup-verify":
        "  link mode: uses no space, but if Steam changes a file in place the backup changes too — check: --backup-verify",
    "\nБекапи гри:":
        "\nGame backups:",
    "\nБекапів гри ще немає (--backup-game).":
        "\nNo game backups yet (--backup-game).",
    "Бекапу «{0}» немає. Є: {1}":
        "There is no backup «{0}». Existing: {1}",
    "Бекап пошкоджений, відновлення скасовано (гру не змінено). Файли не збігаються з записаними: {0}":
        "The backup is damaged, restore cancelled (game not changed). Files do not match the recorded ones: {0}",
    "Спершу прибираю розгорнуті моди...":
        "Removing deployed mods first...",
    "  Замінене й зайве лежить у {0} — нічого не видалено.":
        "  Replaced and extra files are in {0} — nothing was deleted.",
    "Бекапу «{0}» немає.":
        "There is no backup «{0}».",
    "Зберегти бекап чистої гри (папка гри + INI і plugins.txt з prefix). Без NAME — версія й дата.":
        "Save a clean game backup (game folder + INI and plugins.txt from the prefix). Without NAME — version and date.",
    "full — повні копії (безпечно, потрібне місце); link — жорсткі посилання (без місця, не захищає від змін файлів на місці).":
        "full — real copies (safe, needs space); link — hard links (no space, no protection against files changed in place).",
    "З --backup-game: також скопіювати сейви.":
        "With --backup-game: copy the saves too.",
    "Відновити гру з бекапу (моди спершу прибираються; замінене відкладається, не видаляється).":
        "Restore the game from a backup (mods are removed first; replaced files are set aside, not deleted).",
    "Показати бекапи гри.":
        "List game backups.",
    "Перевірити цілісність бекапу (SHA-1 кожного файлу).":
        "Verify a backup (SHA-1 of every file).",
    "Видалити бекап гри.":
        "Delete a game backup.",
    "повні копії":
        "full copies",
    "жорсткі посилання":
        "hard links",
    "Недостатньо місця: потрібно {0}, вільно {1}. Спробуй --backup-mode link (без зайвого місця).":
        "Not enough space: {0} needed, {1} free. Try --backup-mode link (no extra space).",
    "  {0} / {1}":
        "  {0} / {1}",
    ", разом із сейвами":
        ", with saves",
    "  {0} файл(ів) на іншому диску скопійовано повністю":
        "  {0} file(s) on another drive copied in full",
    "  перевірено {0}/{1}...":
        "  checked {0}/{1}...",
    "  Це бекап у режимі link: файл змінили на місці (найчастіше оновлення Steam). Для захисту від оновлень потрібен --backup-mode full.":
        "  This is a link-mode backup: a file was changed in place (usually a Steam update). To protect against updates use --backup-mode full.",
    "файлів":
        "files",
    "Бекап «{0}» цілий.":
        "Backup «{0}» is intact.",
    "Бекап «{0}» пошкоджений: {1} файл(ів) не збігаються, напр. {2}":
        "Backup «{0}» is damaged: {1} file(s) do not match, e.g. {2}",
    "Список файлів гри з SHA-1: {0} файлів, {1} — це займе трохи часу...":
        "Game file list with SHA-1: {0} files, {1} — this takes a while...",
    "Список файлів гри збережено ({0}, гра {1}).":
        "Game file list saved ({0}, game {1}).",
    "\n=== Перший запуск: знімок чистої гри ===":
        "\n=== First run: snapshot of the clean game ===",
    "\n=== Перевірка гри (порівняно зі списком від гри {0}) ===":
        "\n=== Game check (against the file list from game {0}) ===",
    "Ремонт: зайвих відкладено {0}, файлів гри відновлено з бекапу {1}.":
        "Repair: extra files set aside {0}, game files restored from backup {1}.",
    "УВАГА: у грі вже є схоже на моди (у список потрапило як «рідне»): {0}":
        "WARNING: the game already has what looks like mods (listed as the game's own): {0}",
    "Збережу список файлів гри з SHA-1, щоб потім навіть зі зламаної гри знайти й прибрати зайве.":
        "Saving the list of game files with SHA-1, so extras can be found and removed later even from a broken game.",
    "Немає списку файлів гри — створи: --game-baseline":
        "No game file list — create it: --game-baseline",
    "УВАГА: версія гри змінилась: {0} → {1} (оновлення Steam?). Якщо так і треба — онови список: --game-baseline":
        "WARNING: the game version changed: {0} → {1} (Steam update?). If that is intended — refresh the list: --game-baseline",
    "Гра відповідає списку — чиста.":
        "The game matches the list — clean.",
    "Змінені файли гри":
        "Changed game files",
    "Відсутні файли гри":
        "Missing game files",
    "Зайві файли (не з гри і не розгорнуті скриптом)":
        "Extra files (not the game's and not deployed by the script)",
    "Перекрито розгорнутими модами (повернеться через --undeploy): {0}":
        "Overridden by deployed mods (comes back with --undeploy): {0}",
    "Виправити: --game-repair":
        "To fix: --game-repair",
    "  Прибране лежить у {0} — нічого не видалено.":
        "  Removed files are in {0} — nothing was deleted.",
    "Ще {0} файл(ів) гри змінено або бракує, а в бекапах їх немає: Steam → Skyrim → Властивості → Встановлені файли → «Перевірити цілісність файлів гри». Напр.: {1}":
        "{0} more game file(s) are changed or missing and no backup has them: Steam → Skyrim → Properties → Installed Files → “Verify integrity of game files”. E.g.: {1}",
    "Порівняти гру зі списком її файлів (знайти змінене, відсутнє, зайве).":
        "Compare the game with the list of its files (find changed, missing, extra).",
    "Прибрати зайве з гри й повернути змінені файли з бекапів (решту — перевіркою Steam).":
        "Remove extras from the game and restore changed files from backups (the rest — with Steam's verify).",
    "Заново зберегти список файлів гри з SHA-1 (напр., після оновлення Steam).":
        "Save the game file list with SHA-1 again (e.g. after a Steam update).",
    "Зробити ще й бекап чистої гри? [f] повні копії ({0}) / [l] посилання (без місця) / [n] лише список [n]: ":
        "Also make a backup of the clean game? [f] full copies ({0}) / [l] links (no space) / [n] list only [n]: ",
    "Бекап гри можна зробити будь-коли: --backup-game (або в GUI на сторінці «Резервні копії»).":
        "A game backup can be made any time: --backup-game (or in the GUI on the “Backups” page).",
    "  ...і ще {0}":
        "  ...and {0} more",
    "УВАГА: з часу знімку чистої гри змінилось {0} файл(ів) гри (оновлення Steam?) — перевір: --game-check":
        "WARNING: {0} game file(s) changed since the clean game snapshot (Steam update?) — check: --game-check",
    "Інкрементальний бекап: {0} нових оригіналів ({1}); у сховищі {2} файл(ів), {3}. Повернути: --game-repair":
        "Incremental backup: {0} new originals ({1}); in storage {2} file(s), {3}. To restore: --game-repair",
    "Інкрементальний бекап вимкнено (--incremental-backup on, щоб увімкнути)":
        "Incremental backup is off (--incremental-backup on to enable)",
    "Під час розгортання зберігати копію кожного оригінального файлу гри, який перекривають моди (за замовч. увімкнено; запам'ятовується).":
        "On deployment keep a copy of every original game file that mods override (on by default; remembered).",
    "Інкрементальний бекап при розгортанні: {0}. У сховищі {1} файл(ів), {2}.":
        "Incremental backup on deployment: {0}. In storage {1} file(s), {2}.",
    "ПОМИЛКА інкрементального бекапу: {0}. Розгортання скасовано, гру не змінено.":
        "Incremental backup ERROR: {0}. Deployment cancelled, the game was not changed.",
    "увімкнено":
        "on",
    "вимкнено":
        "off",
    "файл {0} змінився під час копіювання":
        "file {0} changed while being copied",
    "Захист гри ще не налаштовано (нічого не робиться без твоєї згоди):":
        "Game protection is not set up yet (nothing is done without your consent):",
    "  --game-baseline — список файлів гри з SHA-1 (рекомендовано); --incremental-backup on — копії оригіналів, які перекривають моди;":
        "  --game-baseline — list of game files with SHA-1 (recommended); --incremental-backup on — copies of originals that mods override;",
    "  --backup-game — повний бекап чистої гри. У GUI — сторінка «Резервні копії». Питання зникне після вибору.":
        "  --backup-game — full backup of the clean game. In the GUI — the “Backups” page. This note goes away once you choose.",
    "\n=== Захист гри (одноразове налаштування) ===":
        "\n=== Game protection (one-time setup) ===",
    "1) Список файлів гри з SHA-1 — щоб навіть зі зламаної гри знайти змінене й прибрати зайве (місця майже не займає, хешування {0} — кілька хвилин).":
        "1) List of game files with SHA-1 — to find changes and remove extras even from a broken game (almost no space; hashing {0} takes a few minutes).",
    "2) Інкрементальний бекап — копія кожного оригінального файлу гри, який перекриває мод (місце — лише ці файли).":
        "2) Incremental backup — a copy of every original game file a mod overrides (space: only those files).",
    "3) Повний бекап чистої гри — повні копії ({0}) або посилання (без місця, але не захищає від змін на місці).":
        "3) Full backup of the clean game — full copies ({0}) or links (no space, but no protection against in-place changes).",
    "Зберегти список файлів (1)? [Y/n]: ":
        "Save the file list (1)? [Y/n]: ",
    "Увімкнути інкрементальний бекап (2)? [Y/n]: ":
        "Enable the incremental backup (2)? [Y/n]: ",
    "Повний бекап (3)? [f] повні копії / [l] посилання / [n] ні [n]: ":
        "Full backup (3)? [f] full copies / [l] links / [n] no [n]: ",
    "Повний бекап можна зробити, коли моди не розгорнуто: --undeploy, --backup-game, --deploy.":
        "A full backup can be made while no mods are deployed: --undeploy, --backup-game, --deploy.",
    "Змінити будь-коли: --game-baseline, --incremental-backup on|off, --backup-game (або в GUI, «Резервні копії»).":
        "Change any time: --game-baseline, --incremental-backup on|off, --backup-game (or in the GUI, “Backups”).",
    "Копіювання файлів гри для захисту вимкнене, доки ти сам не вирішиш (місце на диску):":
        "Copying game files for protection is off until you decide (disk space):",
    "  --incremental-backup on — копії лише тих оригіналів, які перекривають моди; --backup-game — повний бекап чистої гри.":
        "  --incremental-backup on — copies of only the originals that mods override; --backup-game — full backup of the clean game.",
    "  У GUI — сторінка «Резервні копії». Примітка зникне після вибору.":
        "  In the GUI — the “Backups” page. This note goes away once you choose.",
    "1) Інкрементальний бекап — копія кожного оригінального файлу гри, який перекриває мод (місце — лише ці файли).":
        "1) Incremental backup — a copy of every original game file a mod overrides (space: only those files).",
    "2) Повний бекап чистої гри — повні копії ({0}) або посилання (без місця, але не захищає від змін на місці).":
        "2) Full backup of the clean game — full copies ({0}) or links (no space, but no protection against in-place changes).",
    "Увімкнути інкрементальний бекап (1)? [Y/n]: ":
        "Enable the incremental backup (1)? [Y/n]: ",
    "Повний бекап (2)? [f] повні копії / [l] посилання / [n] ні [n]: ":
        "Full backup (2)? [f] full copies / [l] links / [n] no [n]: ",
    "Змінити будь-коли: --incremental-backup on|off, --backup-game (або в GUI, «Резервні копії»).":
        "Change any time: --incremental-backup on|off, --backup-game (or in the GUI, “Backups”).",
    "\n=== Перший запуск: список файлів гри ===":
        "\n=== First run: list of game files ===",
    "архівів":
        "archives",
    "модів":
        "mods",
    "Папка {0}: {1}":
        "The {0} folder: {1}",
    "Папка {0} не може бути папкою гри чи всередині Data: {1}":
        "The {0} folder cannot be the game folder or inside Data: {1}",
    "Папка {0} вже {1}":
        "The {0} folder is already {1}",
    "Нова папка {0} не може бути всередині старої чи навпаки: {1}":
        "The new {0} folder cannot be inside the old one or vice versa: {1}",
    "Моди зараз розгорнуто — спершу --undeploy, потім перенось папку модів.":
        "Mods are deployed right now — run --undeploy first, then move the mods folder.",
    "Переношу вміст {0} → {1}...":
        "Moving contents {0} → {1}...",
    "Папка з архівами на цей запуск (за замовч. збережена через --set-archives, інакше ./mods-archives)":
        "Archives folder for this run (default: the one saved with --set-archives, else ./mods-archives)",
    "Папка з розпакованими модами на цей запуск (за замовч. збережена через --set-mods, інакше ./Mods)":
        "Extracted mods folder for this run (default: the one saved with --set-mods, else ./Mods)",
    "Запам'ятати свою папку з архівами модів ('default' — назад до ./mods-archives).":
        "Remember your own mod archives folder ('default' — back to ./mods-archives).",
    "Запам'ятати свою папку для розпакованих модів ('default' — назад до ./Mods).":
        "Remember your own folder for extracted mods ('default' — back to ./Mods).",
    "З --set-archives/--set-mods: перенести вміст старої папки в нову.":
        "With --set-archives/--set-mods: move the old folder's contents to the new one.",
    "Нова папка {0} не порожня — переносити не буду: {1}":
        "The new {0} folder is not empty — not moving: {1}",
    "У старій папці {0} є файли ({1}); їх не переношу. Перенести: додай --move.":
        "The old {0} folder has files ({1}); not moving them. To move: add --move.",
    "УВАГА: папка модів на іншому диску, ніж гра — розгортання копіюватиме файли замість жорстких посилань (займе місце на диску гри). Краще тримати моди на тому ж диску, що й гру.":
        "WARNING: the mods folder is on another drive than the game — deployment will copy files instead of hard-linking (uses space on the game drive). Better keep mods on the same drive as the game.",
    "Графічний інтерфейс: вікно GTK, якщо є, інакше сторінка в браузері (також: подвійний клік по скрипту). gtk/web — примусово.":
        "Graphical interface: a GTK window if available, otherwise a page in the browser (also: double-click the script). gtk/web — force one.",
    "GTK 3 (PyGObject) недоступний — відкриваю інтерфейс у браузері.":
        "GTK 3 (PyGObject) is not available — opening the interface in the browser.",
    "Створено профіль «{0}»: {1}.":
        "Created profile «{0}»: {1}.",
    "моди з колекції":
        "the collection's mods",
    "усі моди вимкнено, увімкни потрібні":
        "all mods disabled, enable the ones you want",
    "Профіль «{0}»: увімкнено модів колекції {1}, решту ({2}) вимкнено.":
        "Profile «{0}»: {1} collection mods enabled, the rest ({2}) disabled.",
    "  У колекції є вибори FOMOD — застосуй їх для цього профілю: --fomod-redo --auto":
        "  The collection has FOMOD choices — apply them for this profile: --fomod-redo --auto",
    "Профіль «{0}»: усі моди вимкнено — увімкни потрібні (прибери «-» у modlist.txt або в GUI).":
        "Profile «{0}»: all mods disabled — enable the ones you want (remove “-” in modlist.txt or in the GUI).",
    "Створити новий профіль і перемкнутися на нього: усі моди вимкнені, а з --collection/--collection-nexus увімкнені моди колекції.":
        "Create a new profile and switch to it: all mods disabled; with --collection/--collection-nexus the collection mods are enabled.",
}

ARCHIVE_EXTS = {".zip", ".7z", ".rar", ".tar", ".gz", ".bz2", ".xz", ".tgz", ".tar.gz", ".tar.xz", ".tar.bz2"}
PLUGIN_EXTS = {".esp", ".esm", ".esl"}
INTERESTING_EXTS = {
    ".esp": "Plugin", ".esm": "Plugin", ".esl": "Plugin",
    ".dll": "Native library", ".pex": "Papyrus script", ".psc": "Papyrus source",
    ".bsa": "Bethesda archive", ".ba2": "Bethesda archive",
    ".ini": "Configuration", ".json": "JSON data", ".swf": "Flash/UI asset",
    ".nif": "3D model", ".dds": "Texture", ".wav": "Audio", ".xwm": "Audio",
    ".hkx": "Animation/behavior", ".tri": "Morph data", ".strings": "Localization", ".xml": "XML data"
}
NOISE_SUFFIX_PATTERNS = [
    r"-\d{4,}(?:-\d+)*$",                 # Nexus mod ID/version/upload suffix
    r"-\d{4}-\d{2}-\d{2}.*$",             # date-like suffix
    r"-\d{8,}.*$",                        # timestamp-like suffix
]
NEXUS_NAME_RE = re.compile(r"^(.+?)-\d+(?:-\d[0-9A-Za-z]*)*-\d{9,11}$")
# Newer Nexus downloads (seen 2026): "<Name> <ModID> <version> 2026-08-27T15-29Z <random code>".
# Greedy name + one-token version: the last "<ID> <version>" pair before the date is the real one,
# so names that contain numbers ("Skyrim 2020 Parallax") keep them.
NEXUS_ISO_NAME_RE = re.compile(r"^(.+)[ _-](\d+)[ _-](\S+?)[ _-]\d{4}-\d{2}-\d{2}T\d{2}-\d{2}(?:-\d{2})?Z[ _-][0-9A-Za-z]+$")
NEXUS_ISO_TAIL_RE = re.compile(r"[ _-]\d{4}-\d{2}-\d{2}T\d{2}-\d{2}(?:-\d{2})?Z(?:[ _-][0-9A-Za-z]+)?$")
STAGING_PREFIX = ".mod-engine-staging-"

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def human_size(size: int) -> str:
    n = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{size} B"

def clean_mod_name(path: Path) -> str:
    name = path.name
    # Remove only the final extension (including common compound tar extensions).
    lower = name.lower()
    for ext in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if lower.endswith(ext):
            name = name[:-len(ext)]
            break
    else:
        name = path.stem
    name = re.sub(r"\s+", " ", name).strip(" ._-")
    # Nexus download: "<Name>-<ModID>-<version parts>-<unix timestamp>".
    # ModID can be short (USSEP = 266); version parts start with a digit but may contain letters (5-2SE).
    m = NEXUS_NAME_RE.match(name) or NEXUS_ISO_NAME_RE.match(name)
    if m:
        name = m.group(1)
    elif NEXUS_ISO_TAIL_RE.search(name):
        name = NEXUS_ISO_TAIL_RE.sub("", name)  # unknown layout before the date: drop only date+code
    else:
        for pattern in NOISE_SUFFIX_PATTERNS:
            name = re.sub(pattern, "", name, flags=re.IGNORECASE).strip(" ._-")
        name = re.sub(r"\s*-\s*\d{4,}\s*$", "", name)
    name = name.strip(" ._-")
    name = re.sub(r"\.(zip|7z|rar)$", "", name, flags=re.IGNORECASE).strip(" ._-") or name
    # Folder name must be a single safe path component.
    name = name.replace("/", "_").replace("\\", "_")
    if name in ("", ".", ".."):
        name = "Unnamed Mod"
    return name

def is_archive(path: Path) -> bool:
    lower = path.name.lower()
    return any(lower.endswith(ext) for ext in ARCHIVE_EXTS)

def safe_target(root: Path, member: str) -> Path | None:
    # Treat archive paths as POSIX paths and reject absolute paths / traversal.
    normalized = member.replace("\\", "/")
    p = PurePosixPath(normalized)
    if p.is_absolute() or any(part == ".." for part in p.parts):
        return None
    # Reject drive-prefixed paths and empty names.
    if p.parts and re.match(r"^[A-Za-z]:", p.parts[0]):
        return None
    target = (root / Path(*p.parts)).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target

def zip_inventory(path: Path) -> dict:
    files, plugins, top = [], [], set()
    with zipfile.ZipFile(path, "r") as zf:
        for info in zf.infolist():
            name = info.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            if safe_target(Path("/tmp/mod-engine-safety-root"), name) is None:
                continue
            parts = PurePosixPath(name).parts
            if parts:
                top.add(parts[0])
            ext = Path(name).suffix.lower()
            item = {"path": name, "size": info.file_size}
            if ext in INTERESTING_EXTS:
                item["type"] = INTERESTING_EXTS[ext]
            files.append(item)
            if ext in PLUGIN_EXTS:
                plugins.append(name)
        return {
            "format": "zip", "readable": True, "file_count": len(files),
            "plugins": plugins, "top_level": sorted(top),
            "has_data_root": any(x.lower() == "data" for x in top),
            "files": files
        }

TOOL_NAMES = ("7zz", "7z", "bsdtar", "unar", "unrar")
# RAR goes to unrar first: some 7z builds list RAR but lack the RAR codec, and libarchive
# (bsdtar) cannot read some RAR3 solid archives. unrar cannot open anything except RAR.
RAR_ORDER = ("unrar", "7zz", "7z", "bsdtar", "unar")
OTHER_ORDER = ("7zz", "7z", "bsdtar", "unar")
FIRST_VOLUME_RE = re.compile(r"\.part0*1(\.rar)$", re.IGNORECASE)
SECONDARY_VOLUME_RE = re.compile(r"\.part0*(?:[2-9]|[1-9]\d+)\.rar$", re.IGNORECASE)
# Run tools with a UTF-8 locale so non-ASCII names are not mangled into "???" or "\\303..".
TOOL_ENV = {**os.environ, "LC_ALL": "C.UTF-8"}

def find_tools() -> dict[str, str]:
    return {t: p for t in TOOL_NAMES if (p := shutil.which(t))}

def choose_tool(path: Path, tools: dict[str, str]) -> str | None:
    order = RAR_ORDER if path.name.lower().endswith(".rar") else OTHER_ORDER
    for name in order:
        if name in tools:
            return tools[name]
    return None

def run_tool(cmd: list[str], timeout: int) -> subprocess.CompletedProcess:
    # stdin=DEVNULL: an encrypted archive must fail, not hang waiting for a password.
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, errors="replace", timeout=timeout, env=TOOL_ENV)

def drop_dir_entries(names: list[str]) -> list[str]:
    """Remove entries that have other entries inside them: those are folders, even when the archive
    did not flag them as folders (seen: a "Sound/Voice/conquerskyrim.esp" voice folder)."""
    parents = set()
    for n in names:
        parts = n.replace("\\", "/").split("/")
        for k in range(1, len(parts)):
            parents.add("/".join(parts[:k]).lower())
    return [n for n in names if n.replace("\\", "/").rstrip("/").lower() not in parents]

def external_inventory(path: Path, tool: str) -> dict:
    cmd_name = Path(tool).name
    if cmd_name in ("7z", "7zz"):
        cmd = [tool, "l", "-slt", str(path)]
    elif cmd_name == "bsdtar":
        cmd = [tool, "-tf", str(path)]
    elif cmd_name == "unar":
        # unar has no list option; its companion lsar does. -j gives JSON with a directory flag.
        lsar = shutil.which("lsar", path=str(Path(tool).parent)) or shutil.which("lsar")
        if not lsar:
            return {"format": path.suffix.lstrip(".").lower(), "readable": False,
                    "error": tr("Знайдено unar, але немає lsar для перегляду вмісту.")}
        cmd = [lsar, "-j", str(path)]
    elif cmd_name == "unrar":
        # "lt" (technical listing) marks entry types; "lb" mixes directories in with files.
        cmd = [tool, "lt", "-v", "-p-", str(path)]  # -v: include all volumes of a multi-part RAR
    else:
        raise RuntimeError("Unsupported archive utility")
    proc = run_tool(cmd, 180)
    if proc.returncode != 0:
        return {"format": path.suffix.lstrip(".").lower(), "readable": False, "error": proc.stdout[-1500:]}
    lines = proc.stdout.splitlines()
    names = []
    if cmd_name in ("7z", "7zz"):
        # -slt prints one block per entry after the "----------" line. Directories are marked
        # "Folder = +" (p7zip) or "Attributes = D..." (7-Zip 21+); skip them.
        try:
            body = proc.stdout.split("\n----------\n", 1)[1]
        except IndexError:
            body = ""
        for block in body.split("\n\n"):
            fields = {}
            for line in block.splitlines():
                key, sep, value = line.partition(" = ")
                if sep:
                    fields[key.strip()] = value.strip()
            n = fields.get("Path", "")
            if not n:
                continue
            if fields.get("Folder") == "+" or fields.get("Attributes", "").startswith("D"):
                continue
            names.append(n)
    elif cmd_name == "unrar":
        current = None
        for line in lines:
            key, sep, value = line.strip().partition(": ")
            if not sep:
                continue
            if key == "Name":
                current = value
            elif key == "Type" and current is not None:
                if value != "Directory":
                    names.append(current)
                current = None
    elif cmd_name == "unar":
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            return {"format": path.suffix.lstrip(".").lower(), "readable": False, "error": f"lsar JSON: {e}"}
        for entry in data.get("lsarContents", []):
            if entry.get("XADIsDirectory"):
                continue
            n = entry.get("XADFileName", "")
            if n:
                names.append(n)
    else:
        # bsdtar -tf lists paths directly; directories end with "/".
        for line in lines:
            n = line.strip()
            if n:
                names.append(n)
    names = drop_dir_entries(list(dict.fromkeys(names)))  # a file split across RAR volumes is listed once per volume
    files, plugins, top = [], [], set()
    for name in names:
        if name.endswith("/") or safe_target(Path("/tmp/mod-engine-safety-root"), name) is None:
            continue
        parts = PurePosixPath(name.replace("\\", "/")).parts
        if parts:
            top.add(parts[0])
        ext = Path(name).suffix.lower()
        item = {"path": name}
        if ext in INTERESTING_EXTS:
            item["type"] = INTERESTING_EXTS[ext]
        files.append(item)
        if ext in PLUGIN_EXTS:
            plugins.append(name)
    return {
        "format": path.suffix.lstrip(".").lower(), "readable": True,
        "file_count": len(files), "plugins": plugins, "top_level": sorted(top),
        "has_data_root": any(x.lower() == "data" for x in top), "files": files
    }

def extract_zip(path: Path, dest: Path) -> tuple[int, list[str]]:
    count, warnings = 0, []
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "r") as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = safe_target(dest, info.filename)
            if target is None:
                warnings.append(f"Skipped unsafe archive path: {info.filename}")
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info, "r") as src, target.open("wb") as out:
                shutil.copyfileobj(src, out)
            count += 1
    return count, warnings

def extract_external(path: Path, dest: Path, tool: str) -> tuple[int, list[str]]:
    cmd_name = Path(tool).name
    dest.mkdir(parents=True, exist_ok=True)
    if cmd_name in ("7z", "7zz"):
        cmd = [tool, "x", "-y", "-p", f"-o{dest}", str(path)]
    elif cmd_name == "bsdtar":
        cmd = [tool, "-xf", str(path), "-C", str(dest)]
    elif cmd_name == "unar":
        # -D: never create a wrapper directory named after the archive.
        cmd = [tool, "-f", "-D", "-o", str(dest), str(path)]
    elif cmd_name == "unrar":
        cmd = [tool, "x", "-o+", "-p-", "-y", str(path), str(dest) + os.sep]
    else:
        raise RuntimeError("Unsupported archive utility")
    proc = run_tool(cmd, 900)
    if proc.returncode != 0:
        raise RuntimeError(proc.stdout[-2000:] or f"Extractor exited with code {proc.returncode}")
    count = sum(1 for p in dest.rglob("*") if p.is_file())
    return count, []

# ===================================================================================
# Data root detection
# ===================================================================================
# Top-level folders that only exist inside Skyrim's Data directory.
DATA_DIR_NAMES = {
    "meshes", "textures", "scripts", "interface", "sound", "music", "skse", "seq", "strings",
    "shaders", "lodsettings", "grass", "video", "calientetools", "netscriptframework",
    "dialogueviews", "materials", "nemesis_engine", "mcm", "shadersfx", "lightplacer",
    "skyproc patchers",
}
DATA_FILE_EXTS = {".esp", ".esm", ".esl", ".bsa"}
# Files that belong next to SkyrimSE.exe, not inside Data (SKSE loader, ENB, ReShade...).
ROOT_FILE_EXTS = {".exe", ".dll"}
ROOT_DIR_NAMES = {"enbseries", "reshade-shaders"}
# Things that never decide the layout: readmes, screenshots, links.
IGNORED_EXTS = {".txt", ".md", ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".url",
                ".htm", ".html", ".rtf", ".doc", ".docx", ".odt", ".nfo"}
IGNORED_DIR_NAMES = {"screenshots", "images", "docs", "documentation", "readme", "__macosx"}

def ci_children(d: Path) -> dict[str, Path]:
    try:
        return {p.name.lower(): p for p in d.iterdir()}
    except OSError:
        return {}

def ci_join(base: Path, *parts: str) -> Path | None:
    """Case-insensitive path lookup: Linux is case-sensitive, Skyrim mods are not."""
    cur = base
    for part in parts:
        nxt = ci_children(cur).get(part.lower())
        if nxt is None:
            return None
        cur = nxt
    return cur

def is_ignorable(p: Path) -> bool:
    if p.is_dir():
        return p.name.lower() in IGNORED_DIR_NAMES
    return p.suffix.lower() in IGNORED_EXTS

def data_score(d: Path, allow_ini: bool = True) -> int:
    score = 0
    loose_ini = 0
    for name, p in ci_children(d).items():
        if p.is_dir() and name in DATA_DIR_NAMES:
            score += 1
        elif p.is_file() and p.suffix.lower() in DATA_FILE_EXTS:
            score += 1
        elif p.is_file() and p.suffix.lower() == ".ini":
            loose_ini += 1
    # INI files in the Data root are configs for SKSE frameworks (SPID *_DISTR.ini, KID *_KID.ini,
    # Base Object Swapper *_SWAP.ini, Music Type Distributor *_MUS.ini...) or plugin INIs.
    # They count only when nothing points to the game folder (an ENB preset also has .ini files).
    if allow_ini and loose_ini and not score and not has_root_files(d):
        score += 1
    return score

def is_skse_plugin_dll(p: Path) -> bool:
    try:
        return any(n.startswith("SKSEPlugin_") for n in PEFile(p).exports)
    except Exception:
        return False

def has_root_files(d: Path) -> bool:
    for name, p in ci_children(d).items():
        if p.is_file() and p.suffix.lower() in ROOT_FILE_EXTS and not is_skse_plugin_dll(p):
            return True
        if p.is_dir() and name in ROOT_DIR_NAMES:
            return True
    return False

def detect_layout(mod: Path) -> dict:
    """Find which folder inside an extracted mod corresponds to Skyrim's Data.

    layout values:
      data           — data_root found; mod can be deployed as-is
      root+data      — Data folder plus files for the game folder (SKSE, some ENB packs)
      root-only      — only game-folder files (ENB binaries, ReShade)
      loose-skse     — SKSE plugin .dll lying loose, without Data/SKSE/Plugins around it
      fomod          — FOMOD installer; files depend on options chosen in the installer
      options        — several alternative folders, each looks like Data; user must pick
      unknown        — nothing recognizable
    """
    rel = lambda p: "." if p == mod else str(p.relative_to(mod))
    out = {"layout": "unknown", "data_root": None, "root_dir": None, "options": [], "notes": []}
    cur = mod
    for _ in range(5):
        kids = ci_children(cur)
        if ci_join(cur, "fomod", "moduleconfig.xml"):
            out["layout"] = "fomod"
            out["fomod_root"] = rel(cur)
            out["options"] = sorted(rel(p) for n, p in kids.items()
                                    if p.is_dir() and n != "fomod" and not is_ignorable(p))
            out["notes"].append(tr("FOMOD-інсталятор: вміст залежить від вибраних опцій"))
            return out
        if data_score(cur) > 0:
            out["layout"], out["data_root"] = "data", rel(cur)
            root = kids.get("root")  # Mod Organizer convention: Root/ goes to the game folder
            if root is not None and root.is_dir():
                out["layout"], out["root_dir"] = "root+data", rel(root)
            return out
        data = kids.get("data")
        if data is not None and data.is_dir():
            out["data_root"] = rel(data)
            if has_root_files(cur):
                out["layout"], out["root_dir"] = "root+data", rel(cur)
            else:
                out["layout"] = "data"
            return out
        meaningful = [p for p in kids.values() if not is_ignorable(p)]
        subdirs = [p for p in meaningful if p.is_dir()]
        files = [p for p in meaningful if p.is_file()]
        if len(subdirs) == 1 and not files:
            cur = subdirs[0]  # wrapper folder like "ModName v1.2/"
            continue
        break
    kids = ci_children(cur)
    candidates = sorted(rel(p) for p in kids.values()
                        if p.is_dir() and p.name.lower() not in ROOT_DIR_NAMES
                        and (data_score(p, allow_ini=False) > 0 or ci_join(p, "data")))
    if len(candidates) > 1:
        out["layout"], out["options"] = "options", candidates
        out["notes"].append(tr("Кілька альтернативних папок, схожих на Data — потрібен вибір"))
    elif len(candidates) == 1:
        # One Data-like folder next to other stuff: use it, but say so.
        sub = detect_layout_at(mod, mod / candidates[0])
        out.update(sub)
        out["notes"].append(tr("Поруч з {0} є інші файли — перевір вручну", candidates[0]))
    elif has_root_files(cur):
        out["layout"], out["root_dir"] = "root-only", rel(cur)
    else:
        loose = [p.name for p in kids.values() if p.is_file() and is_skse_plugin_dll(p)]
        if loose:
            out["layout"] = "loose-skse"
            out["options"] = sorted(loose)
            out["notes"].append(tr("SKSE-плагін без структури Data — його місце в Data/SKSE/Plugins"))
    return out

def detect_layout_at(mod: Path, sub: Path) -> dict:
    r = detect_layout(sub)
    prefix = str(sub.relative_to(mod))
    for key in ("data_root", "root_dir", "fomod_root"):
        if r.get(key) is not None:
            r[key] = prefix if r[key] == "." else f"{prefix}/{r[key]}"
    r["options"] = [f"{prefix}/{o}" for o in r["options"]]
    return r

# ===================================================================================
# Compact per-mod summary + gzip file index (instead of full file lists in manifest)
# ===================================================================================
def summarize_mod(mod: Path, index_dir: Path) -> dict:
    if FOMOD_OUT_RE.match(mod.name):  # FOMOD output ("[FOMOD]", "[FOMOD 2]"...) is built as Data by definition
        lay = {"layout": "data", "data_root": ".", "root_dir": None, "options": [], "notes": []}
    else:
        lay = detect_layout(mod)
    data = None if lay["data_root"] is None else (mod if lay["data_root"] == "." else mod / lay["data_root"])
    counts: dict[str, int] = {}
    total_size = 0
    file_count = 0
    plugins, plugin_variants, bsa, skse_dlls = [], [], [], []
    rows = []
    for p in mod.rglob("*"):
        if not p.is_file() or p.is_symlink():
            continue
        st = p.stat()
        file_count += 1
        total_size += st.st_size
        kind = INTERESTING_EXTS.get(p.suffix.lower(), "Other")
        counts[kind] = counts.get(kind, 0) + 1
        rel_mod = p.relative_to(mod).as_posix()
        if data is not None and (p == data or data in p.parents):
            rel_data = p.relative_to(data).as_posix()
            rows.append(f"{rel_data}\t{st.st_size}")
            parts = rel_data.lower().split("/")
            ext = p.suffix.lower()
            if len(parts) == 1 and ext in PLUGIN_EXTS:
                plugins.append(rel_data)
            elif len(parts) == 1 and ext == ".bsa":
                bsa.append(rel_data)
            elif ext in PLUGIN_EXTS:
                plugin_variants.append(rel_data)  # deeper .esp is never loaded by the game
            if len(parts) == 3 and parts[0] == "skse" and parts[1] == "plugins" and ext == ".dll":
                skse_dlls.append(rel_data)
        elif p.suffix.lower() in PLUGIN_EXTS:
            plugin_variants.append(rel_mod)
    # One small gzip TSV per mod: "path-relative-to-Data <TAB> size". Used later for conflicts.
    index_dir.mkdir(parents=True, exist_ok=True)
    index_file = index_dir / (re.sub(r"[^\w.() -]", "_", mod.name) + ".tsv.gz")
    with gzip.open(index_file, "wt", encoding="utf-8", compresslevel=6) as f:
        f.write("\n".join(sorted(rows)))
    summary = {
        "name": mod.name,
        "layout": lay["layout"],
        "data_root": lay["data_root"],
        "file_count": file_count,
        "size": human_size(total_size),
        "size_bytes": total_size,
        "counts": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "plugins": sorted(plugins, key=str.lower),
        "index": index_file.name,
    }
    # Optional keys only when non-empty, so typical entries stay short.
    for key, val in (("fomod_root", lay.get("fomod_root")), ("root_dir", lay["root_dir"]), ("options", lay["options"]), ("notes", lay["notes"]),
                     ("bsa", sorted(bsa)), ("plugin_variants", sorted(plugin_variants)[:30]),
                     ("skse_plugins", sorted(skse_dlls))):
        if val:
            summary[key] = val
    if len(plugin_variants) > 30:
        summary["plugin_variants_total"] = len(plugin_variants)
    return summary

# ===================================================================================
# SKSE: game version, SKSE presence, Address Library, plugin DLL compatibility, log
# ===================================================================================
SKYRIM_APPID = "489830"
GAME_DIR_NAME = "Skyrim Special Edition"
MY_GAMES_NAMES = ("Skyrim Special Edition", "Skyrim Special Edition GOG", "Skyrim Special Edition EPIC")
# SKSEPluginVersionData flags (skse64 PluginAPI.h).
VI_ADDRESS_LIBRARY = 1 << 0
VI_SIGNATURES = 1 << 1
VI_STRUCTS_POST_629 = 1 << 2
VIEX_NO_STRUCT_USE = 1 << 0

def pack_runtime(v: tuple[int, ...]) -> int:
    # MAKE_EXE_VERSION(major, minor, build) from skse64_common
    return ((v[0] & 0xFF) << 24) | ((v[1] & 0xFF) << 16) | ((v[2] & 0xFFF) << 4)

def unpack_runtime(x: int) -> str:
    return f"{x >> 24}.{(x >> 16) & 0xFF}.{(x >> 4) & 0xFFF}"

def steam_libraries() -> list[Path]:
    roots = [Path.home() / ".local/share/Steam", Path.home() / ".steam/steam"]
    libs: list[Path] = []
    for r in roots:
        vdf = r / "steamapps" / "libraryfolders.vdf"
        if r.exists():
            libs.append(r)
        if vdf.exists():
            for m in re.finditer(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace")):
                libs.append(Path(m.group(1).replace("\\\\", "/")))
    seen, out = set(), []
    for lib in libs:
        key = str(lib.resolve()) if lib.exists() else str(lib)
        if key not in seen:
            seen.add(key)
            out.append(lib)
    return out

def find_game_dir(explicit: str | None) -> Path | None:
    # The script usually sits in the game folder itself: that folder wins over Steam libraries.
    cands = [Path(explicit).expanduser()] if explicit else [Path.cwd(), Path(__file__).resolve().parent] + [
        lib / "steamapps" / "common" / GAME_DIR_NAME for lib in steam_libraries()]
    for c in cands:
        if c.is_dir() and ci_join(c, "SkyrimSE.exe"):
            return c
    return None

def find_skse_log(game_dir: Path, explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit).expanduser()
        return p if p.is_file() else None
    # Proton keeps the game's "Documents" inside the compatdata prefix of the same Steam library.
    steamapps = game_dir.parent.parent
    docs = steamapps / "compatdata" / SKYRIM_APPID / "pfx/drive_c/users/steamuser/Documents/My Games"
    for name in MY_GAMES_NAMES:
        log = ci_join(docs, name, "SKSE", "skse64.log") if docs.exists() else None
        if log and log.is_file():
            return log
    return None

def pe_file_version(path: Path) -> tuple[int, int, int, int] | None:
    """FileVersion from the VS_FIXEDFILEINFO block of a Windows .exe/.dll."""
    with path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
        i = mm.find(b"\xbd\x04\xef\xfe")
        if i < 0:
            return None
        ms, ls = struct.unpack_from("<II", mm, i + 8)
        return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)

class PEFile:
    """Minimal PE32+ reader: exported names and reading bytes at an RVA. Stdlib only."""
    def __init__(self, path: Path):
        self.data = path.read_bytes()
        d = self.data
        if d[:2] != b"MZ":
            raise ValueError(tr("не PE-файл (немає MZ)"))
        pe = struct.unpack_from("<I", d, 0x3C)[0]
        if d[pe:pe + 4] != b"PE\0\0":
            raise ValueError(tr("не PE-файл (немає PE)"))
        self.machine, nsec = struct.unpack_from("<HH", d, pe + 4)
        opt_size = struct.unpack_from("<H", d, pe + 20)[0]
        opt = pe + 24
        magic = struct.unpack_from("<H", d, opt)[0]
        if magic != 0x20B:
            raise ValueError(tr("32-бітний PE — SKSE64 такий не завантажить"))
        self.sections = []
        sec = opt + opt_size
        for k in range(nsec):
            o = sec + 40 * k
            vsize, va, rsize, raw = struct.unpack_from("<IIII", d, o + 8)
            self.sections.append((va, max(vsize, rsize), raw))
        exp_rva, exp_size = struct.unpack_from("<II", d, opt + 112)
        self.exports: dict[str, int] = {}
        if exp_rva:
            e = self.off(exp_rva)
            nfuncs, nnames, funcs, names, ords = struct.unpack_from("<IIIII", d, e + 20)
            for j in range(nnames):
                name_rva = struct.unpack_from("<I", d, self.off(names) + 4 * j)[0]
                ordinal = struct.unpack_from("<H", d, self.off(ords) + 2 * j)[0]
                func_rva = struct.unpack_from("<I", d, self.off(funcs) + 4 * ordinal)[0]
                no = self.off(name_rva)
                self.exports[d[no:d.index(b"\0", no)].decode("ascii", "replace")] = func_rva

    def off(self, rva: int) -> int:
        for va, size, raw in self.sections:
            if va <= rva < va + size:
                return raw + (rva - va)
        raise ValueError(tr("RVA {0:#x} поза секціями", rva))

    def read(self, rva: int, n: int) -> bytes:
        o = self.off(rva)
        return self.data[o:o + n]

def read_skse_version_data(pe: PEFile) -> dict | None:
    rva = pe.exports.get("SKSEPlugin_Version")
    if rva is None:
        return None
    raw = pe.read(rva, 856)
    if len(raw) < 848:
        return None
    cstr = lambda b: b.split(b"\0", 1)[0].decode("utf-8", "replace")
    compat = [x for x in struct.unpack_from("<16I", raw, 780) if x]
    return {
        "data_version": struct.unpack_from("<I", raw, 0)[0],
        "plugin_version": struct.unpack_from("<I", raw, 4)[0],
        "name": cstr(raw[8:264]),
        "author": cstr(raw[264:520]),
        "independence_ex": struct.unpack_from("<I", raw, 772)[0],
        "independence": struct.unpack_from("<I", raw, 776)[0],
        "compatible_versions": compat,
        "se_version_required": struct.unpack_from("<I", raw, 844)[0],
    }

def check_plugin_dll(path: Path, runtime: tuple[int, ...]) -> dict:
    """Static prediction of whether SKSE will load this DLL on this game runtime."""
    res = {"status": "unknown", "detail": ""}
    try:
        pe = PEFile(path)
    except Exception as e:
        res.update(status="unreadable", detail=str(e))
        return res
    vd = read_skse_version_data(pe)
    has_query = "SKSEPlugin_Query" in pe.exports
    has_load = "SKSEPlugin_Load" in pe.exports
    if vd:
        res["name"] = vd["name"]
        res["needs_address_library"] = bool(vd["independence"] & VI_ADDRESS_LIBRARY)
    if not (vd or has_query or has_load):
        res.update(status="not-a-plugin", detail=tr("немає експортів SKSEPlugin_* — допоміжна DLL або не SKSE"))
        return res
    if runtime >= (1, 6, 0):  # Anniversary-era runtime: SKSE requires SKSEPlugin_Version
        if not vd:
            res.update(status="incompatible", detail=tr("немає SKSEPlugin_Version — плагін для старої SE 1.5.97"))
            return res
        rt = pack_runtime(runtime)
        indep = vd["independence"]
        if indep & (VI_ADDRESS_LIBRARY | VI_SIGNATURES):
            version_ok, how = True, tr("незалежний від версії (Address Library/сигнатури)")
        elif rt in vd["compatible_versions"]:
            version_ok, how = True, tr("явно підтримує {0}", unpack_runtime(rt))
        else:
            listed = ", ".join(unpack_runtime(v) for v in vd["compatible_versions"]) or tr("жодної")
            res.update(status="incompatible", detail=tr("зібраний під {0}, гра {1}", listed, unpack_runtime(rt)))
            return res
        if (indep & (VI_ADDRESS_LIBRARY | VI_SIGNATURES)) and runtime >= (1, 6, 629) \
                and not (indep & VI_STRUCTS_POST_629) and not (vd["independence_ex"] & VIEX_NO_STRUCT_USE):
            res.update(status="incompatible", detail=tr("використовує структури до 1.6.629"))
            return res
        res.update(status="ok", detail=how)
    else:  # SE 1.5.x: loader calls SKSEPlugin_Query
        if not has_query:
            res.update(status="incompatible", detail=tr("лише SKSEPlugin_Version — плагін тільки для AE (1.6+)"))
            return res
        res.update(status="ok", detail=tr("має SKSEPlugin_Query (остаточно версію перевіряє сам плагін під час запуску)"))
    return res

def address_library_name(runtime: tuple[int, ...]) -> str:
    v = "-".join(str(x) for x in (list(runtime) + [0, 0, 0, 0])[:4])
    return f"versionlib-{v}.bin" if runtime >= (1, 6, 0) else f"version-{v}.bin"

def log_dll_names(line: str) -> list[str]:
    """DLL file names in a log line; names may contain spaces ("Better Jumping SE.dll")."""
    names = []
    for m in re.finditer(r"\.dll\b", line, re.IGNORECASE):
        start = max(line.rfind("\\", 0, m.start()), line.rfind("/", 0, m.start()))
        if start < 0:  # no path, just a bare name: take the last word
            start = line.rfind(" ", 0, m.start())
        names.append(line[start + 1:m.end()].strip())
    return names

LOG_REASONS = (
    ("fatal error occurred while loading", tr("впав під час завантаження")),
    ("reported as incompatible during load", tr("сам відмовився працювати (несумісна версія гри чи залежності)")),
    ("reported as incompatible during query", tr("сам відмовився працювати (несумісна версія гри)")),
    ("incompatible with current runtime", tr("зібраний під іншу версію гри")),
    ("no version data", tr("не SKSE-плагін або плагін для старої SE 1.5.97")),
)
WIN_ERRORS = {126: tr("бракує DLL, від якої він залежить"), 127: tr("бракує функції в іншій DLL"),
              193: tr("не 64-бітна або пошкоджена DLL"), 998: tr("помилка доступу до пам'яті під час завантаження DLL")}

def parse_skse_log(log: Path) -> dict:
    """Classify each plugin line of skse64.log (format checked against a real SKSE 2.2.6 log:
    'plugin <name>.dll (<ver> <name> <ver>) loaded correctly / disabled, fatal error ... /
    couldn't load plugin <code> / reported as incompatible during load / no version data')."""
    out: dict[str, dict] = {}
    runtime = None
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        low = line.lower()
        m = re.search(r"runtime:\s*initialize\s*\(version\s*=\s*[\d.]+\s+([0-9a-f]{8})", low)
        if m:
            runtime = unpack_runtime(int(m.group(1), 16))
        m = re.search(r"plugin\s+(.+?\.dll)(?=\s*\(|\s*$)", line, re.IGNORECASE)
        if not m:
            continue
        dll = re.split(r"[\\/]", m.group(1).strip())[-1]
        key = dll.lower()
        if key.startswith("skse64_"):
            continue
        entry = out.setdefault(key, {"dll": dll, "status": "seen", "line": "", "reason": ""})
        if "loaded correctly" in low:
            entry.update(status="loaded", line=line.strip())
            continue
        for phrase, why in LOG_REASONS:
            if phrase in low:
                entry.update(status="info" if phrase == "no version data" else "failed", reason=why, line=line.strip())
                break
        else:
            c = re.search(r"couldn't load plugin\s+(\d+)", low) or re.search(r"error\s+(\d+)", low)
            if "couldn't load" in low and not c:
                entry.update(status="failed", line=line.strip(), reason=tr("Windows не зміг завантажити DLL"))
            elif c:
                code = int(c.group(1))
                entry.update(status="failed", line=line.strip(),
                             reason=tr("Windows не зміг завантажити DLL (код {0}: {1})", code, WIN_ERRORS.get(code, tr('невідома помилка'))))
            elif entry["status"] == "seen":
                entry["line"] = line.strip()
    plugins = sorted(out.values(), key=lambda e: e["dll"].lower())
    return {"log": str(log), "runtime": runtime, "plugins": plugins,
            "fatal": sum(1 for p in plugins if p["reason"] == tr("впав під час завантаження"))}

def skse_check(game_dir: Path, mods_root: Path, mod_summaries: list[dict], log_arg: str | None,
               order: list[str] | None = None) -> dict:
    report: dict = {"game_dir": str(game_dir)}
    exe = ci_join(game_dir, "SkyrimSE.exe")
    gv = pe_file_version(exe) if exe else None
    report["game_version"] = ".".join(map(str, gv)) if gv else None
    runtime = gv[:3] if gv else None
    srs = find_srs(game_dir, mods_root)
    if srs and runtime and runtime in (vtuple(srs["sourceVersion"]), vtuple(srs["targetVersion"])):
        # With SRS the game that SKSE and its plugins actually run against is the target version
        report["srs"] = {"source": srs["sourceVersion"], "target": srs["targetVersion"], "deployed": srs["_deployed"]}
        runtime = vtuple(srs["targetVersion"])
        report["runtime_checked"] = ".".join(map(str, runtime))

    loader = ci_join(game_dir, "skse64_loader.exe")
    dlls = sorted(p.name for p in game_dir.iterdir() if re.fullmatch(r"skse64_\d+_\d+_\d+\.dll", p.name, re.I))
    skse = {"loader": bool(loader), "runtime_dlls": dlls, "matches_game": False}
    if loader:
        lv = pe_file_version(loader)
        skse["loader_version"] = ".".join(map(str, lv)) if lv else None
    if runtime:
        want = "skse64_{}_{}_{}.dll".format(*runtime)
        skse["expected_dll"] = want
        skse["matches_game"] = any(d.lower() == want for d in dlls)
    report["skse"] = skse

    # Address Library: look in the game's Data and in every extracted mod.
    al = {"expected": address_library_name(runtime) if runtime else None, "found_in": []}
    plugins: list[dict] = []
    sources: list[tuple[str, Path]] = []
    gdata = ci_join(game_dir, "Data")
    if gdata:
        sources.append((tr("[гра] Data"), gdata))
    for s in mod_summaries:
        if s.get("data_root") is not None:
            base = mods_root / s["name"]
            sources.append((s["name"], base if s["data_root"] == "." else base / s["data_root"]))
    mod_inodes = set()
    for label, data in sources[1:] if gdata else sources:
        pd = ci_join(data, "SKSE", "Plugins")
        if pd:
            mod_inodes |= {(q.stat().st_dev, q.stat().st_ino) for q in pd.iterdir() if q.is_file()}
    # The same DLL in several mods: the one later in the mod order is what ends up in the game.
    pos = {n: i for i, n in enumerate(order or [])}
    winner: dict[str, str] = {}
    for label, data in sources:
        if label == tr("[гра] Data"):
            continue
        pd = ci_join(data, "SKSE", "Plugins")
        if pd:
            for q in pd.iterdir():
                k = q.name.lower()
                if k not in winner or pos.get(label, -1) >= pos.get(winner[k], -1):
                    winner[k] = label
    for label, data in sources:
        pdir = ci_join(data, "SKSE", "Plugins")
        if not pdir:
            continue
        for p in sorted(pdir.iterdir(), key=lambda x: x.name.lower()):
            if label != tr("[гра] Data") and winner.get(p.name.lower(), label) != label:
                if p.suffix.lower() == ".dll":
                    plugins.append({"source": label, "dll": p.name, "status": "overridden",
                                    "detail": tr("перекрито копією з «{0}»", winner[p.name.lower()])})
                continue
            if label == tr("[гра] Data") and p.is_file() and (p.stat().st_dev, p.stat().st_ino) in mod_inodes:
                continue  # hard link created by --deploy: reported under its mod
            if al["expected"] and p.name.lower() == al["expected"].lower():
                al["found_in"].append(label)
            if p.is_file() and p.suffix.lower() == ".dll" and runtime:
                r = check_plugin_dll(p, runtime)
                r = {"source": label, "dll": p.name, **r}
                plugins.append(r)
    report["address_library"] = al
    report["plugins"] = plugins
    if any(p.get("needs_address_library") for p in plugins) and not al["found_in"]:
        report.setdefault("warnings", []).append(
            tr("Є плагіни, яким потрібна Address Library, але {0} не знайдено ні в грі, ні в модах", al['expected']))
    if runtime and dlls and not skse["matches_game"]:
        report.setdefault("warnings", []).append(
            tr("SKSE зібраний під іншу версію гри ({0}), гра {1}", ', '.join(dlls), report.get('runtime_checked') or report['game_version']))
    if runtime and not loader:
        report.setdefault("warnings", []).append(tr("skse64_loader.exe не знайдено в папці гри"))

    log = find_skse_log(game_dir, log_arg)
    if log:
        report["log"] = parse_skse_log(log)
    return report

def print_skse_report(r: dict) -> None:
    print("\n=== SKSE ===")
    print(tr("Гра: {0}", r['game_dir']))
    print(tr("Версія гри: {0}", r.get('game_version') or tr('не вдалося визначити')))
    if r.get("srs"):
        st = tr("розгорнуто") if r["srs"]["deployed"] else tr("лише в Mods — ще не розгорнуто")
        print(tr("SRS ({0}): SKSE і плагіни перевіряються під {1}", st, r['srs']['target']))
    s = r["skse"]
    if s["loader"]:
        ok = tr("так") if s["matches_game"] else tr("НІ")
        print(tr("SKSE: є ({0}); відповідає грі: {1}", ', '.join(s['runtime_dlls']) or tr('без runtime DLL'), ok))
    else:
        print(tr("SKSE: не встановлено в папці гри"))
    al = r["address_library"]
    if al["expected"]:
        print(f"Address Library ({al['expected']}): {', '.join(al['found_in']) or tr('не знайдено')}")
    marks = {"ok": "OK ", "incompatible": tr("НІ "), "not-a-plugin": " ? ", "unreadable": "ERR", "unknown": " ? ",
             "overridden": " = "}
    if r["plugins"]:
        print(tr("Плагіни (статичний прогноз):"))
        for p in r["plugins"]:
            al_mark = " [AL]" if p.get("needs_address_library") else ""
            print(f"  [{marks.get(p['status'], ' ? ')}] {p['source']}: {p['dll']}{al_mark} — {p['detail']}")
    for w in r.get("warnings", []):
        print(tr("УВАГА: {0}", w))
    if "log" in r:
        lg = r["log"]
        print(tr("Лог останнього запуску: {0}", lg['log']))
        if lg["runtime"]:
            print(tr("  runtime у лозі: {0}", lg['runtime']))
        loaded = [p for p in lg["plugins"] if p["status"] == "loaded"]
        print(tr("  завантажено: {0}; не завантажено: {1}", len(loaded), sum(1 for p in lg['plugins'] if p['status'] != 'loaded')))
        for p in lg["plugins"]:
            if p["status"] == "loaded":
                continue
            mark = {"failed": tr("НІ "), "info": " - "}.get(p["status"], " ? ")
            print(f"  [{mark}] {p['dll']} — {p['reason'] or p['line'][:150]}")
        if lg.get("fatal", 0) >= 3:
            print(tr("\n  Багато плагінів «впали під час завантаження». Під Proton найчастіша причина — у prefix гри"))
            print(tr("  немає справжнього Visual C++ runtime (вбудована заміна Wine неповна). Спробуй встановити його:"))
            print("    flatpak run com.github.Matoking.protontricks 489830 vcrun2022")
            print(tr("  (Protontricks є в Discover). Це найімовірніша причина, але не гарантована."))
    else:
        print(tr("Лог skse64.log не знайдено (гра ще не запускалась через SKSE?)"))

# ===================================================================================
# Plugin header reader (TES4 record): masters a plugin depends on
# ===================================================================================
BASE_MASTERS = ("skyrim.esm", "update.esm", "dawnguard.esm", "hearthfires.esm", "dragonborn.esm")

def read_plugin_masters(path: Path) -> list[str] | None:
    """Masters listed in the TES4 header of an .esp/.esm/.esl. None if the file isn't a plugin."""
    try:
        with path.open("rb") as f:
            head = f.read(24)
            if len(head) < 24 or head[:4] != b"TES4":
                return None
            size = struct.unpack_from("<I", head, 4)[0]
            body = f.read(min(size, 1 << 20))
    except OSError:
        return None
    masters, i, big = [], 0, None
    while i + 6 <= len(body):
        sig = body[i:i + 4]
        sz = struct.unpack_from("<H", body, i + 4)[0]
        i += 6
        if sig == b"XXXX":  # next subrecord is larger than 64 KiB
            big = struct.unpack_from("<I", body, i)[0]
            i += sz
            continue
        if big is not None:
            sz, big = big, None
        if sig == b"MAST":
            masters.append(body[i:i + sz].split(b"\0", 1)[0].decode("cp1252", "replace"))
        i += sz
    return masters

# ===================================================================================
# FOMOD installer
# ===================================================================================
# Rules match pyfomod's installer: an omitted destination equals the source; a <file> with an
# empty or "/"-terminated destination goes into that folder under its own name; with equal
# priority the later entry wins; installSteps/groups/plugins default to order="Ascending".
FOMOD_SUFFIX = " [FOMOD]"
GROUP_RULES = {
    "SelectExactlyOne": tr("рівно один"),
    "SelectAtMostOne": tr("не більше одного"),
    "SelectAtLeastOne": tr("хоча б один"),
    "SelectAll": tr("усі"),
    "SelectAny": tr("будь-які"),
}

def load_fomod_config(fomod_root: Path) -> ET.Element:
    cfg = ci_join(fomod_root, "fomod", "ModuleConfig.xml")
    if cfg is None:
        raise FileNotFoundError(tr("fomod/ModuleConfig.xml не знайдено"))
    data = cfg.read_bytes()
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        # Real-world files sometimes declare one encoding and use another.
        for enc in ("utf-8-sig", "utf-16", "cp1252"):
            try:
                text = data.decode(enc).lstrip("﻿").lstrip()
                root = ET.fromstring(re.sub(r"^<\?xml[^>]*\?>", "", text))
                break
            except (UnicodeDecodeError, ET.ParseError):
                continue
        else:
            raise
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root

def ordered(elements: list[ET.Element], order: str | None) -> list[ET.Element]:
    order = order or "Ascending"
    if order == "Explicit":
        return list(elements)
    return sorted(elements, key=lambda e: (e.get("name") or "").lower(), reverse=(order == "Descending"))

class FomodEnv:
    """What the installer knows about the setup: available plugins, SKSE, popularity of masters."""
    def __init__(self, plugins: set[str], skse: bool, master_use: dict[str, int],
                 mod_names: dict[str, set[str]] | None = None):
        self.mod_names = mod_names or {}  # display name -> tokens (mod folders and plugin names)
        self.plugins = plugins          # lower-case plugin names present in game Data or other mods
        self.skse = skse
        self.master_use = master_use    # master (lower) -> how many of your mods depend on it

    def file_state(self, name: str) -> str:
        # Load order isn't managed yet: anything present counts as Active.
        return "Active" if name.lower() in self.plugins else "Missing"

def eval_deps(el: ET.Element | None, flags: dict[str, str], env: FomodEnv) -> bool:
    if el is None:
        return True
    results = []
    for c in el:
        if c.tag == "flagDependency":
            results.append(flags.get(c.get("flag", ""), "") == (c.get("value") or ""))
        elif c.tag == "fileDependency":
            results.append(env.file_state(c.get("file", "")) == c.get("state", "Active"))
        elif c.tag == "dependencies":
            results.append(eval_deps(c, flags, env))
        else:  # gameDependency, fommDependency, foseDependency...: version checks, assume satisfied
            results.append(True)
    if not results:
        return True
    return all(results) if (el.get("operator") or "And").lower() == "and" else any(results)

def plugin_type(pl: ET.Element, flags: dict[str, str], env: FomodEnv) -> str:
    td = pl.find("typeDescriptor")
    if td is None:
        return "Optional"
    t = td.find("type")
    if t is not None:
        return t.get("name", "Optional")
    dt = td.find("dependencyType")
    if dt is None:
        return "Optional"
    for pat in dt.findall("patterns/pattern"):
        if eval_deps(pat.find("dependencies"), flags, env):
            ty = pat.find("type")
            if ty is not None:
                return ty.get("name", "Optional")
    d = dt.find("defaultType")
    return d.get("name", "Optional") if d is not None else "Optional"

def fomod_file_ops(files_el: ET.Element | None, src_root: Path, warnings: list[str],
                   only_flag: str | None = None) -> list[tuple[str, Path, int]]:
    """Expand <file>/<folder> entries into (destination-in-Data, source file, priority)."""
    ops: list[tuple[str, Path, int]] = []
    if files_el is None:
        return ops
    for el in files_el:
        if el.tag not in ("file", "folder"):
            continue
        if only_flag and (el.get(only_flag) or "").lower() != "true":
            continue
        source = (el.get("source") or "").replace("\\", "/").strip("/")
        dest = el.get("destination")
        dest = source if dest is None else dest.replace("\\", "/")
        prio = int(el.get("priority") or 0)
        src = ci_join(src_root, *[p for p in source.split("/") if p]) if source else src_root
        if src is None or (src.is_symlink()):
            warnings.append(tr("джерело не знайдено: {0}", source))
            continue
        if el.tag == "file":
            if dest == "" or dest.endswith("/"):
                dest = dest + Path(source).name
            if src.is_file():
                ops.append((dest.strip("/"), src, prio))
            else:
                warnings.append(tr("<file> вказує не на файл: {0}", source))
        else:
            if not src.is_dir():
                warnings.append(tr("<folder> вказує не на папку: {0}", source))
                continue
            base = dest.strip("/")
            for f in sorted(src.rglob("*")):
                if f.is_file() and not f.is_symlink():
                    rel = f.relative_to(src).as_posix()
                    ops.append((f"{base}/{rel}" if base else rel, f, prio))
    return ops

def key_files(ops: list[tuple[str, Path, int]]) -> list[str]:
    """Top-level plugins and BSAs: two options installing the same one are alternatives."""
    return [d.lower() for d, _, _ in ops if "/" not in d and Path(d).suffix.lower() in PLUGIN_EXTS | {".bsa"}]

def top_plugins(ops: list[tuple[str, Path, int]]) -> list[tuple[str, Path]]:
    return [(d, s) for d, s, _ in ops if "/" not in d and Path(d).suffix.lower() in PLUGIN_EXTS]

# ---- Requirements stated only in the option description ("REQUIRES AC3S's LAKEVIEW EXTENDED MOD!") ----
REQ_STOPWORDS = {
    "the", "a", "an", "mod", "mods", "version", "versions", "se", "sse", "ae", "le", "plugin", "plugins",
    "addon", "add", "on", "of", "by", "installed", "latest", "newest", "newer", "or", "is", "be", "must",
    "esp", "esm", "esl", "files", "file", "main", "first", "you", "your", "it", "this", "also", "both",
    "need", "needs", "require", "required", "requires", "loaded", "active", "game", "special", "edition",
}
REQ_ALWAYS_PRESENT = {"hearthfire", "hearthfires", "dawnguard", "dragonborn", "dlc", "dlcs", "skyrim",
                      "anniversary", "update", "creation", "club"}
REQ_ALIASES = {"ussep": "unofficial skyrim special edition patch", "uskp": "unofficial skyrim patch",
               "sky ui": "skyui"}

def req_tokens(text: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in REQ_STOPWORDS and not t.isdigit()}

def description_requirements(desc: str) -> list[list[str]]:
    """Required things named in a description; each item is a list of alternatives ("X or Y")."""
    items = []
    for m in re.finditer(r"\brequires?\b[:\s]+([^.!\n]{2,120})", desc, re.IGNORECASE):
        phrase = re.sub(r"\b[\w-]+['’]s\b", " ", m.group(1))           # drop author possessive
        phrase = re.split(r"\b(?:to|for|if|in order|which|that|so|with)\b", phrase, flags=re.I)[0]
        for part in re.split(r"\s*(?:,|&|\+|\band\b)\s*", phrase, flags=re.I):
            alts = []
            for alt in re.split(r"\bor\b", part, flags=re.I):
                low = alt.lower().strip()
                for k, v in REQ_ALIASES.items():
                    low = re.sub(rf"\b{k}\b", v, low)
                toks = req_tokens(low)
                if not toks:
                    continue  # "or newer", "or later"
                if "skse" in toks or toks <= REQ_ALWAYS_PRESENT:
                    alts = []  # SKSE is checked separately; DLCs are part of SE/AE
                    break
                alts.append(" ".join(sorted(toks)))
            if alts:
                items.append(alts)
    return items

def find_requirement(alt: str, names: dict[str, set[str]]) -> str | None:
    want = set(alt.split())
    for name, toks in names.items():
        found = len(want & toks)
        if found == len(want) or (len(want) >= 3 and found / len(want) >= 0.75):
            return name
    return None

def score_option(name: str, ops: list[tuple[str, Path, int]], ftype: str, env: FomodEnv,
                 extra_plugins: set[str], description: str = "") -> tuple[float, list[str], bool]:
    """Auto-mode preference for one option. Returns (score, reasons, usable)."""
    score, why, usable = 0.0, [], True
    low = name.lower().replace("-", " ").replace("_", " ")
    if ftype == "NotUsable":
        return -100, [tr("FOMOD позначає як непридатний")], False
    if ftype == "Required":
        score += 50
        why.append(tr("обов'язковий у FOMOD"))
    elif ftype == "Recommended":
        score += 1
        why.append(tr("рекомендований автором"))
    available = env.plugins | extra_plugins
    for dest, src in top_plugins(ops):
        masters = read_plugin_masters(src)
        if masters is None:
            continue
        missing = [m for m in masters if m.lower() not in available]
        if missing:
            usable = False
            why.append(tr("{0}: немає master {1}", dest, ', '.join(missing)))
            continue
        for m in masters:
            ml = m.lower()
            if ml in BASE_MASTERS:
                continue
            uses = env.master_use.get(ml, 0)
            score += 3 + min(uses, 5)  # patch for a mod you have; the more mods rely on it, the stronger
            why.append(tr("залежить від {0}, який у тебе є", m) + (tr(" (і ще {0} мод(ів) від нього залежать)", uses) if uses else ""))
    for alts in description_requirements(description):
        hit = next((h for a in alts if (h := find_requirement(a, env.mod_names))), None)
        label = " / ".join(alts)
        if hit:
            score += 1
            why.append(tr("вимога з опису «{0}» є: {1}", label, hit))
        else:
            score -= 3
            why.append(tr("в описі вимагає «{0}» — схожого мода в тебе не знайдено", label))
    ships_skse_dll = any(d.lower().startswith("skse/plugins/") and d.lower().endswith(".dll") for d, _, _ in ops)
    says_no_skse = bool(re.search(r"\b(no|non|without|без)\s*skse\b", low))
    # The requirement is often only in the description: "normal ... Requires SKSE".
    desc_low = description.lower()
    desc_no_skse = bool(re.search(r"\b(no|non|without)[\s-]*skse\b", desc_low))
    desc_needs_skse = bool(re.search(r"\brequires?\b[^.!]*\bskse\b", desc_low)) and not desc_no_skse
    says_no_skse = says_no_skse or (desc_no_skse and "skse" not in low.replace("no skse", ""))
    says_skse = ("skse" in low and not says_no_skse) or (desc_needs_skse and not says_no_skse)
    if env.skse:
        if says_no_skse:
            score -= 2
            why.append(tr("варіант без SKSE, а SKSE встановлено"))
        elif says_skse or ships_skse_dll:
            score += 2
            why.append(tr("SKSE встановлено"))
    else:
        if ships_skse_dll:
            usable = False
            why.append(tr("містить SKSE-плагін, а SKSE не встановлено"))
        elif says_skse:
            score -= 5
            why.append(tr("потребує SKSE, а його немає"))
        elif says_no_skse:
            score += 2
            why.append(tr("SKSE не встановлено"))
    return score, why, usable

def auto_pick(gtype: str, scored: list[dict], main_version: bool = False) -> list[int]:
    # Options that would overwrite the plugin/BSA of an option picked in an earlier group are
    # alternatives of it ("-OR-" groups): never auto-pick them.
    usable = [i for i, o in enumerate(scored) if o["usable"] and not o.get("conflict")]
    required = [i for i in usable if scored[i]["type"] == "Required"]
    if gtype == "SelectAll":
        return usable
    best = max(usable, key=lambda i: (scored[i]["score"], -i), default=None)
    if gtype in ("SelectExactlyOne",):
        return [best] if best is not None else []
    if gtype == "SelectAtMostOne":
        # A group that picks the main version of the mod (nothing chosen so far ships a plugin)
        # should not default to "nothing": take the best non-negative option.
        threshold = 0 if main_version else 1e-9
        return [best] if best is not None and scored[best]["score"] >= threshold else []
    picked = sorted(set(required) | {i for i in usable if scored[i]["score"] > 0})
    if gtype == "SelectAtLeastOne" and not picked and best is not None:
        picked = [best]
    return picked

def validate_pick(gtype: str, picked: list[int], scored: list[dict]) -> str | None:
    for i in picked:
        if scored[i].get("conflict"):
            return (tr("«{0}» ставить ті самі файли, що вже вибраний «{1}» ({2}) — це альтернативи, встановився б лише один. Залиш цю групу порожньою (просто Enter) або почни заново з --fomod-redo", scored[i]['name'], scored[i]['conflict'], scored[i]['conflict_file']))
        if not scored[i]["usable"]:
            return tr("«{0}» непридатний: {1}", scored[i]['name'], '; '.join(scored[i]['why']))
    n = len(picked)
    if gtype == "SelectExactlyOne" and n != 1:
        return tr("треба вибрати рівно один")
    if gtype == "SelectAtMostOne" and n > 1:
        return tr("можна вибрати не більше одного")
    if gtype == "SelectAtLeastOne" and n < 1:
        return tr("треба вибрати хоча б один")
    return None

def ask_group(step_name: str, gname: str, gtype: str, scored: list[dict], default: list[int]) -> list[int]:
    print(f"\n  [{step_name}] {gname} — {GROUP_RULES.get(gtype, gtype)}")
    if any(o.get("conflict") for o in scored):
        print(tr("   (варіанти, позначені «альтернатива», замінили б уже вибране вище)"))
    for i, o in enumerate(scored, 1):
        mark = "*" if (i - 1) in default else " "
        flag = "" if o["usable"] else tr("  (непридатний)")
        if o.get("conflict"):
            flag += tr("  (альтернатива до «{0}»)", o['conflict'])
        print(f"   {i}) [{mark}] {o['name']}{flag}")
        d = o["description"]
        if d:
            short = d if len(d) <= 220 else d[:217] + "..."
            for line in textwrap.wrap(short, 100):
                print(f"        {line}")
            reqs = re.findall(r"\brequires?\b[:\s]+([^.!\n]{3,90})", d, re.IGNORECASE)
            if reqs:
                print(tr("        вимагає: {0}", '; '.join(r.strip() for r in reqs)))
        if o["why"]:
            print(tr("        авто: {0}", '; '.join(o['why'])[:160]))
    if gtype == "SelectAll":
        print(tr("   (встановлюються всі)"))
        return default
    none_ok = gtype in ("SelectAtMostOne", "SelectAny")
    hint = tr(", 0 = нічого") if none_ok else ""
    while True:
        try:
            raw = input(tr("  Вибір (номери через пробіл, Enter = {0}{1}): ", ' '.join(str(i + 1) for i in default) or tr('нічого'), hint)).strip()
        except EOFError:
            raw = ""
        if raw == "":
            return default
        if raw in ("0", "-") and none_ok:
            req = [i for i, o in enumerate(scored) if o["type"] == "Required" and o["usable"]]
            return req
        try:
            picked = sorted({int(x) - 1 for x in re.split(r"[\s,]+", raw) if x})
        except ValueError:
            print(tr("  Вкажи номери."))
            continue
        if any(i < 0 or i >= len(scored) for i in picked):
            print(tr("  Немає такого номера."))
            continue
        req = [i for i, o in enumerate(scored) if o["type"] == "Required" and o["usable"]]
        picked = sorted(set(picked) | set(req))
        err = validate_pick(gtype, picked, scored)
        if err:
            print(f"  {err}")
            continue
        return picked

def run_fomod(mod_dir: Path, fomod_rel: str, env: FomodEnv, interactive: bool,
              saved: dict | None) -> tuple[list[tuple[str, Path, int]], dict, list[str]]:
    """Walk the installer. Returns (file operations in apply order, choices record, warnings)."""
    src_root = mod_dir if fomod_rel == "." else mod_dir / fomod_rel
    cfg = load_fomod_config(src_root)
    warnings: list[str] = []
    flags: dict[str, str] = {}
    ops: list[tuple[str, Path, int]] = []
    choices: dict = {"steps": [], "module": (cfg.findtext("moduleName") or "").strip()}

    md = cfg.find("moduleDependencies")
    if md is not None and not eval_deps(md, flags, env):
        warnings.append(tr("умови moduleDependencies не виконані (бракує потрібних модів?)"))

    ops += fomod_file_ops(cfg.find("requiredInstallFiles"), src_root, warnings)

    steps_el = cfg.find("installSteps")
    steps = ordered(steps_el.findall("installStep"), steps_el.get("order")) if steps_el is not None else []
    # Plugins selected in this run count as available for later masters checks.
    selected_plugins: set[str] = {d.lower() for d, _ in top_plugins(ops)}
    picked_keys: dict[str, str] = {}  # plugin/BSA file -> option that installs it
    # Author's choices from a Vortex collection: a list of the steps the author went through, in order.
    # Step names can repeat, so steps are matched in sequence rather than by name alone.
    coll_opts = saved.get("collection_options") if saved else None
    coll_ptr = 0
    ops_cache: dict[int, list] = {}

    def option_ops(pl: ET.Element) -> list:
        if id(pl) not in ops_cache:
            ops_cache[id(pl)] = fomod_file_ops(pl.find("files"), src_root, [])
        return ops_cache[id(pl)]
    for step in steps:
        sname = step.get("name", "")
        if not eval_deps(step.find("visible/dependencies") if step.find("visible/dependencies") is not None
                         else step.find("visible"), flags, env):
            continue
        coll_step = None
        if coll_opts is not None:
            j = next((j for j in range(coll_ptr, len(coll_opts)) if (coll_opts[j].get("name") or "") == sname), None)
            if j is None:
                j = next((j for j in range(coll_ptr, len(coll_opts)) if not coll_opts[j].get("name")), None)
            if j is not None:
                coll_step, coll_ptr = coll_opts[j], j + 1
        groups_el = step.find("optionalFileGroups")
        groups = ordered(groups_el.findall("group"), groups_el.get("order")) if groups_el is not None else []
        step_rec = {"step": sname, "groups": []}
        # Look ahead across this step's groups: for every plugin/BSA file, which option installing it
        # scores best. "-OR-" groups are alternatives, so the best one should win, not simply the first.
        best_for_key: dict[str, tuple[float, int]] = {}
        for gi, g in enumerate(groups):
            for pl in g.findall("plugins/plugin"):
                pops = option_ops(pl)
                desc = " ".join((pl.findtext("description") or "").split())
                sc, _, ok = score_option(pl.get("name", ""), pops, plugin_type(pl, flags, env), env,
                                         selected_plugins, desc)
                if not ok:
                    continue
                for k in key_files(pops):
                    if k not in best_for_key or sc > best_for_key[k][0]:
                        best_for_key[k] = (sc, gi)
        for gi, g in enumerate(groups):
            gname, gtype = g.get("name", ""), g.get("type", "SelectAny")
            pls_el = g.find("plugins")
            pls = ordered(pls_el.findall("plugin"), pls_el.get("order")) if pls_el is not None else []
            scored = []
            for pl in pls:
                ftype = plugin_type(pl, flags, env)
                pops = option_ops(pl)
                desc = " ".join((pl.findtext("description") or "").split())
                sc, why, usable = score_option(pl.get("name", ""), pops, ftype, env, selected_plugins, desc)
                scored.append({"name": pl.get("name", ""), "type": ftype, "score": sc, "why": why,
                               "usable": usable, "el": pl, "ops": pops,
                               "description": " ".join((pl.findtext("description") or "").split())})
            for o in scored:
                hit = next((k for k in key_files(o["ops"]) if k in picked_keys), None)
                if hit:
                    shown = next((d for d, _, _ in o["ops"] if d.lower() == hit), hit)
                    o["conflict"], o["conflict_file"] = picked_keys[hit], shown
            main_version = not selected_plugins and any(top_plugins(o["ops"]) for o in scored)
            for o in scored:
                better = [best_for_key[k] for k in key_files(o["ops"])
                          if k in best_for_key and best_for_key[k][1] != gi and best_for_key[k][0] > o["score"]]
                if better and not o.get("conflict") and o["type"] != "Required":
                    o["deferred"] = True  # a later/earlier alternative group has a better option
            default = auto_pick(gtype, [dict(o, usable=o["usable"] and not o.get("deferred")) for o in scored],
                                main_version)
            prev, prev_manual, from_saved, from_coll = None, False, False, False
            if coll_opts is not None:
                rec = next((g for g in (coll_step or {}).get("groups", []) if g.get("name") == gname), None)
                if rec is not None:
                    names_ = [c.get("name") for c in rec.get("choices", [])]
                    idx = [i for i, o in enumerate(scored) if o["name"] in names_]
                    if len(idx) != len(names_):  # names differ: fall back to the recorded positions
                        idx = [c.get("idx") for c in rec.get("choices", [])
                               if isinstance(c.get("idx"), int) and 0 <= c["idx"] < len(scored)]
                    n = len(idx)
                    count_ok = not ((gtype == "SelectExactlyOne" and n != 1) or (gtype == "SelectAtMostOne" and n > 1)
                                    or (gtype == "SelectAtLeastOne" and n < 1)) and len(idx) == len(names_)
                    if count_ok:
                        default, from_saved, from_coll, prev_manual = sorted(idx), True, True, True
                        for i in idx:
                            if not scored[i]["usable"]:
                                warnings.append(tr("автор колекції обрав «{0}», хоча: {1}", scored[i]['name'], '; '.join(scored[i]['why'])))
                    else:
                        warnings.append(tr("вибір автора для «{0}» не зіставився з інсталятором — взято авто-вибір", gname))
                else:
                    warnings.append(tr("для «{0} / {1}» у колекції немає вибору — взято авто-вибір", sname, gname))
            elif saved:
                rec = next((gr for st in saved.get("steps", []) if st["step"] == sname
                            for gr in st["groups"] if gr["group"] == gname), None)
                if rec is not None:
                    prev = rec["selected"]
                    prev_manual = rec.get("manual", saved.get("mode") == "interactive")
            if prev is not None:
                idx = [i for i, o in enumerate(scored) if o["name"] in prev]
                # Your own manual choice is kept unless it became unusable; a previous auto choice
                # is also replaced when the setup now points elsewhere (negative score).
                bad = [] if prev_manual else [scored[i]["name"] for i in idx if scored[i]["score"] < 0]
                if len(idx) == len(prev) and validate_pick(gtype, idx, scored) is None and not bad:
                    default = idx
                    from_saved = True
                else:
                    warnings.append(tr("збережений вибір для «{0}» більше не підходить до поточного набору модів — взято авто-вибір", gname))
            picked = ask_group(sname, gname, gtype, scored, default) if interactive else default
            if not interactive and from_saved:
                names = ", ".join(scored[i]["name"] for i in picked) or tr("нічого")
                src_label = tr("вибір автора колекції") if from_coll else tr("збережений {0}-вибір", tr('ручний') if prev_manual else tr('авто'))
                print(f"  [{sname}] {gname}: {names}  ({src_label})")
            elif not interactive:
                names = ", ".join(scored[i]["name"] for i in picked) or tr("нічого")
                print(f"  [{sname}] {gname}: {names}")
                if not picked and any(o.get("deferred") for o in scored):
                    print(tr("      (кращий варіант — в іншій групі-альтернативі цього кроку)"))
                for i in picked:
                    if scored[i]["why"]:
                        print(tr("      чому «{0}»: {1}", scored[i]['name'], '; '.join(scored[i]['why'])))
                if gtype in ("SelectExactlyOne", "SelectAtMostOne") and picked:
                    top = scored[picked[0]]["score"]
                    ties = [o["name"] for j, o in enumerate(scored) if o["usable"] and o["score"] == top and j not in picked]
                    if ties:
                        print(tr("      рівноцінні за залежностями: {0} — взято перший; це питання смаку,", ', '.join(ties)))
                        print(tr("      змінити можна через --fomod-redo без --auto"))
            for i in picked:
                o = scored[i]
                for f in o["el"].findall("conditionFlags/flag"):
                    flags[f.get("name", "")] = (f.text or "").strip()
                ops += o["ops"]
                selected_plugins |= {d.lower() for d, _ in top_plugins(o["ops"])}
                for k in key_files(o["ops"]):
                    picked_keys.setdefault(k, o["name"])
            # Files marked alwaysInstall / installIfUsable inside options that were not picked.
            for i, o in enumerate(scored):
                if i in picked:
                    continue
                ops += fomod_file_ops(o["el"].find("files"), src_root, warnings, only_flag="alwaysInstall")
                if o["type"] != "NotUsable":
                    ops += fomod_file_ops(o["el"].find("files"), src_root, warnings, only_flag="installIfUsable")
            # Interactive answers (even Enter) are the user's decision; a reused manual choice stays manual.
            manual = interactive or (from_saved and prev_manual)
            step_rec["groups"].append({"group": gname, "selected": [scored[i]["name"] for i in picked],
                                       "manual": manual})
        choices["steps"].append(step_rec)

    for pat in cfg.findall("conditionalFileInstalls/patterns/pattern"):
        if eval_deps(pat.find("dependencies"), flags, env):
            ops += fomod_file_ops(pat.find("files"), src_root, warnings)
    choices["flags"] = flags
    return ops, choices, warnings

def ci_dest(root: Path, rel: str) -> Path:
    """Destination path that reuses the case of folders already created (Textures vs textures)."""
    cur = root
    parts = [p for p in rel.split("/") if p not in ("", ".")]
    for j, part in enumerate(parts):
        if part == "..":
            raise ValueError(tr("небезпечний шлях: {0}", rel))
        existing = ci_children(cur).get(part.lower()) if cur.exists() else None
        cur = existing if existing is not None else cur / part
        if j < len(parts) - 1:
            cur.mkdir(parents=True, exist_ok=True)
    return cur

def apply_fomod(ops: list[tuple[str, Path, int]], dest_root: Path) -> int:
    final: dict[str, tuple[int, str, Path]] = {}
    for dest, src, prio in ops:  # later entry wins on equal priority
        key = dest.lower()
        if key in final and final[key][0] > prio:
            continue
        final[key] = (prio, dest, src)
    dest_root.mkdir(parents=True, exist_ok=True)
    # The output folder starts empty, so folder case (Textures vs textures) is resolved in memory:
    # the first spelling seen wins, and every folder is created once. (Reading the folder from disk
    # for every file made big texture packs quadratic: 8000 files took ~4 minutes.)
    dir_case: dict[str, str] = {}
    for _, dest, src in final.values():
        parts = [x for x in dest.split("/") if x not in ("", ".")]
        if not parts or ".." in parts:
            raise ValueError(tr("небезпечний шлях: {0}", dest))
        real, low = "", ""
        for part in parts[:-1]:
            low = f"{low}/{part.lower()}" if low else part.lower()
            if low not in dir_case:
                dir_case[low] = f"{real}/{part}" if real else part
                (dest_root / dir_case[low]).mkdir(exist_ok=True)
            real = dir_case[low]
        target = dest_root / (f"{real}/{parts[-1]}" if real else parts[-1])
        try:
            os.link(src, target)  # hard link: no extra disk space on the Deck
        except OSError:
            shutil.copy2(src, target)
    return len(final)

def build_fomod_env(game_dir: Path | None, mods_root: Path, summaries: list[dict]) -> FomodEnv:
    plugins = set(BASE_MASTERS)
    mod_names: dict[str, set[str]] = {}
    for s in summaries:
        mod_names[s["name"]] = req_tokens(s["name"])
        for pl in s.get("plugins", []):
            mod_names[pl] = req_tokens(Path(pl).stem)
    skse = False
    master_use: dict[str, int] = {}
    if game_dir:
        gdata = ci_join(game_dir, "Data")
        if gdata:
            plugins |= {p.name.lower() for p in gdata.iterdir() if p.suffix.lower() in PLUGIN_EXTS}
        skse = ci_join(game_dir, "skse64_loader.exe") is not None
    for s in summaries:
        if s["layout"] not in ("data", "root+data"):
            continue
        plugins |= {p.lower() for p in s.get("plugins", [])}
        if s.get("root_dir") is not None:
            rd = mods_root / s["name"] if s["root_dir"] == "." else mods_root / s["name"] / s["root_dir"]
            if ci_join(rd, "skse64_loader.exe"):
                skse = True
        data = mods_root / s["name"] if s["data_root"] == "." else mods_root / s["name"] / s["data_root"]
        for pl in s.get("plugins", []):
            for m in read_plugin_masters(data / pl) or []:
                master_use[m.lower()] = master_use.get(m.lower(), 0) + 1
    if game_dir and (gd := ci_join(game_dir, "Data")):
        for p in gd.iterdir():
            if p.suffix.lower() in PLUGIN_EXTS:
                mod_names[p.name] = req_tokens(p.stem)
    return FomodEnv(plugins, skse, master_use, mod_names)

def fomod_selection_key(rec: dict) -> str:
    """What was selected, ignoring how (manual/auto) — equal keys install equal files."""
    return json.dumps([[st.get("step"), [[g.get("group"), sorted(g.get("selected") or [])] for g in st.get("groups", [])]]
                       for st in rec.get("steps") or []], ensure_ascii=False)

def install_fomods(mods_root: Path, output_root: Path, summaries: list[dict], game_dir: Path | None,
                   interactive: bool, redo: bool, coll_choices: dict | None = None,
                   profile: str = DEFAULT_PROFILE, pdir: Path | None = None) -> tuple[list[str], dict[str, str]]:
    """Install every raw FOMOD mod for this profile. Returns (new folders, renames old -> new folder).
    The profile's folder is reinstalled in place unless another profile uses it; then the new choice
    goes to "<mod> [FOMOD N]". Two profiles with identical choices share one folder."""
    done: list[str] = []
    renames: dict[str, str] = {}
    pdir = pdir or output_root
    folders = {p.name for p in mods_root.iterdir() if p.is_dir()}
    raw = {s["name"] for s in summaries if s["layout"] == "fomod"}
    owned = fomod_owned(pdir, raw, folders)
    elsewhere = fomod_used_elsewhere(output_root, profile, raw, folders) if pdir != output_root else {}
    other_recs = {p: fomod_choice_records(profile_dir(output_root, p))
                  for p in list_profiles(output_root) if p != profile} if pdir != output_root else {}
    redo_names = set(owned.values()) if redo else set()
    view = profile_view(summaries, owned)
    env = build_fomod_env(game_dir, mods_root, [s for s in view if s["name"] not in redo_names])
    choices_dir = pdir / "fomod"
    choices_dir.mkdir(parents=True, exist_ok=True)
    print(tr("\n=== FOMOD === (SKSE: {0}; відомих плагінів: {1})", tr('є') if env.skse else tr('немає'), len(env.plugins)))
    todo = [s for s in summaries if s["layout"] == "fomod"]
    for k, s in enumerate(todo, 1):
        name = s["name"]
        cur = owned.get(name)
        saved_file = choices_dir / (re.sub(r"[^\w.() -]", "_", name) + ".json")
        if cur and not redo:
            print(tr("[{0}/{1}] {2}: уже встановлено ({3}); --fomod-redo щоб перевстановити", k, len(todo), name, cur))
            continue
        saved = None
        if saved_file.exists():
            try:
                saved = json.loads(saved_file.read_text(encoding="utf-8"))
            except Exception:
                saved = None
        if coll_choices and name in coll_choices:
            saved = {"collection_options": coll_choices[name], "mode": "collection"}
        print(f"\n[{k}/{len(todo)}] {name}" + (tr(" (вибір автора колекції)") if saved and saved.get("mode") == "collection"
                               else tr(" (є збережений вибір)") if saved else ""))
        try:
            ops, choices, warnings = run_fomod(mods_root / name, s.get("fomod_root", "."), env, interactive, saved)
        except Exception as e:
            print(tr("  ПОМИЛКА FOMOD: {0}", e))
            continue
        for w in warnings:
            print(tr("  увага: {0}", w))
        if not ops:
            print(tr("  Вибір не дає жодного файлу — нічого не встановлено."))
            continue
        sel = fomod_selection_key(choices)
        # The same choice already installed by another profile: share its folder.
        shared = None
        for p, recs in other_recs.items():
            r = recs.get(name)
            if r and r.get("installed_to") in folders and fomod_selection_key(r) == sel:
                shared = (p, r["installed_to"])
                break
        if shared and shared[1] != cur:
            out_name, n = shared[1], other_recs[shared[0]][name].get("file_count", 0)
            print(tr("  Такий самий вибір уже встановлено для профілю «{0}» — використовую {1}", shared[0], out_name))
        else:
            if shared or (cur and cur not in elsewhere):
                out_name = cur  # ours alone (or identical to what the other profile has): reinstall in place
            else:
                out_name = name + FOMOD_SUFFIX
                v = 2
                while out_name in folders:
                    out_name = f"{name} [FOMOD {v}]"
                    v += 1
                if cur:
                    print(tr("  {0} використовує також профіль(і) {1} — новий вибір встановлюю в {2}", cur, ", ".join(elsewhere[cur]), out_name))
            out_dir = mods_root / out_name
            staging = mods_root / f"{STAGING_PREFIX}{out_name}"
            shutil.rmtree(staging, ignore_errors=True)
            try:
                n = apply_fomod(ops, staging)
                if out_dir.exists():
                    shutil.rmtree(out_dir)
                staging.rename(out_dir)
            except Exception as e:
                shutil.rmtree(staging, ignore_errors=True)
                print(tr("  ПОМИЛКА встановлення: {0}", e))
                continue
            folders.add(out_name)
            print(tr("  Готово: {0} файлів -> {1}", n, out_name))
        choices.update(mod=name, installed_to=out_name, file_count=n,
                       mode="interactive" if interactive else "auto",
                       at=datetime.now().astimezone().isoformat(timespec="seconds"))
        saved_file.write_text(json.dumps(choices, ensure_ascii=False, indent=1), encoding="utf-8")
        if cur and cur != out_name:
            renames[cur] = out_name
            m = FOMOD_OUT_RE.match(cur)
            if m and m.group(2) and cur not in elsewhere:
                shutil.rmtree(mods_root / cur, ignore_errors=True)  # numbered variant nobody uses any more
                folders.discard(cur)
        owned[name] = out_name
        done.append(out_name)
    return done, renames

# ===================================================================================
# BSA file lists (Skyrim LE v104 / SE v105)
# ===================================================================================
def read_bsa_names(path: Path) -> list[str] | None:
    """Paths stored in a .bsa ("textures/foo/bar.dds"). Only headers and name tables are read."""
    try:
        with path.open("rb") as f:
            head = f.read(36)
            if len(head) < 36 or head[:4] != b"BSA\0":
                return None
            version, offset, aflags, nfolders, nfiles, _dlen, flen = struct.unpack_from("<7I", head, 4)
            if version not in (103, 104, 105):
                return None
            rec_size = 24 if version == 105 else 16
            f.seek(offset)
            counts = [struct.unpack_from("<I", f.read(rec_size), 8)[0] for _ in range(nfolders)]
            folders = []
            for c in counts:
                name = ""
                if aflags & 0x1:  # directory names present: 1-byte length (with trailing NUL) + name
                    n = f.read(1)[0]
                    name = f.read(n).split(b"\0", 1)[0].decode("cp1252", "replace")
                f.seek(16 * c, 1)  # file records
                folders.append((name, c))
            if not aflags & 0x2:  # file names not stored: nothing to compare
                return None
            names = f.read(flen).split(b"\0")
    except (OSError, IndexError, struct.error):
        return None
    out, k = [], 0
    for folder, c in folders:
        for _ in range(c):
            if k >= len(names):
                break
            fname = names[k].decode("cp1252", "replace")
            k += 1
            out.append(f"{folder.replace(chr(92), '/')}/{fname}" if folder else fname)
    return out

# ===================================================================================
# Profiles: mod_engine_output/profiles/<name>/ holds modlist.txt (order, "-" = disabled),
# fomod/ (this profile's FOMOD choices) and overwrite/ (files the game or tools created or
# changed in Data while this profile was deployed). Mods/ is shared by all profiles.
# A FOMOD mod installed with different choices in two profiles gets two folders:
# "<mod> [FOMOD]" and "<mod> [FOMOD 2]"; each profile only sees its own.
# ===================================================================================
PROFILES_DIR = "profiles"
OVERWRITE_NAME = "[Overwrite]"
PROFILE_NAME_RE = re.compile(r"[\w][\w .()-]{0,39}")
FOMOD_OUT_RE = re.compile(r"^(.*) \[FOMOD(?: (\d+))?\]$")

def profiles_root(output_root: Path) -> Path:
    return output_root / PROFILES_DIR

def list_profiles(output_root: Path) -> list[str]:
    root = profiles_root(output_root)
    return sorted((p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")),
                  key=str.lower) if root.exists() else []

def read_settings(output_root: Path) -> dict:
    try:
        return json.loads((output_root / SETTINGS_FILE).read_text(encoding="utf-8"))
    except Exception:
        return {}

def update_settings(output_root: Path, **kv) -> None:
    data = read_settings(output_root)
    data.update(kv)
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / SETTINGS_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

def migrate_to_profiles(output_root: Path) -> None:
    """v0.9 kept modlist.txt and fomod/ directly in mod_engine_output: they become the Default profile."""
    root = profiles_root(output_root)
    if root.exists():
        return
    d = root / DEFAULT_PROFILE
    d.mkdir(parents=True)
    if (output_root / "modlist.txt").exists():
        shutil.move(str(output_root / "modlist.txt"), d / "modlist.txt")
    if (output_root / "fomod").is_dir():
        shutil.move(str(output_root / "fomod"), d / "fomod")

def profile_dir(output_root: Path, name: str) -> Path:
    return profiles_root(output_root) / name

def fomod_choice_records(pdir: Path) -> dict[str, dict]:
    """Raw FOMOD mod name -> saved choice record of this profile."""
    out = {}
    for f in sorted((pdir / "fomod").glob("*.json")) if (pdir / "fomod").is_dir() else []:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if rec.get("mod"):
            out[rec["mod"]] = rec
    return out

def fomod_owned(pdir: Path, raw_fomods: set[str], folders: set[str]) -> dict[str, str]:
    """Raw FOMOD mod -> its installed folder in this profile. Without a saved record the plain
    "<mod> [FOMOD]" folder counts as the profile's (installs made before profiles existed)."""
    owned = {}
    recs = fomod_choice_records(pdir)
    for raw in raw_fomods:
        rec = recs.get(raw)
        folder = rec.get("installed_to") if rec else raw + FOMOD_SUFFIX
        if folder in folders:
            owned[raw] = folder
    return owned

def fomod_used_elsewhere(output_root: Path, profile: str, raw_fomods: set[str], folders: set[str]) -> dict[str, list[str]]:
    """FOMOD folder -> other profiles that use it."""
    used: dict[str, list[str]] = {}
    for p in list_profiles(output_root):
        if p != profile:
            for f in fomod_owned(profile_dir(output_root, p), raw_fomods, folders).values():
                used.setdefault(f, []).append(p)
    return used

def profile_view(summaries: list[dict], owned: dict[str, str]) -> list[dict]:
    """Catalog as one profile sees it: FOMOD output folders of other profiles are left out."""
    raw = {s["name"] for s in summaries if s["layout"] == "fomod"}
    mine = set(owned.values())
    out = []
    for s in summaries:
        m = FOMOD_OUT_RE.match(s["name"])
        if m and m.group(1) in raw and s["name"] not in mine:
            continue
        out.append(s)
    return out

def overwrite_summary(pdir: Path) -> dict | None:
    """The profile's overwrite folder as a pseudo-mod deployed above every mod."""
    ow = pdir / "overwrite"
    if not ow.is_dir() or not any(p.is_file() for p in ow.rglob("*")):
        return None
    plugins = sorted(p.name for p in ow.iterdir() if p.is_file() and p.suffix.lower() in PLUGIN_EXTS)
    return {"name": OVERWRITE_NAME, "layout": "data", "data_root": ".", "abs_data": str(ow), "plugins": plugins}

def create_profile(output_root: Path, src: str, name: str, init: str = "empty") -> bool:
    """A new profile starts with every mod disabled (init "empty") — or, when a collection comes with
    it (init "collection"), with exactly the collection's mods enabled. Only the mod order is taken
    from the current profile; FOMOD choices and the overwrite folder start empty."""
    if not PROFILE_NAME_RE.fullmatch(name):
        print(tr("Недопустима назва профілю: «{0}» (літери, цифри, пробіл, . ( ) - _; до 40 символів)", name))
        return False
    if name.lower() in (p.lower() for p in list_profiles(output_root)):
        print(tr("Профіль «{0}» уже існує.", name))
        return False
    s, d = profile_dir(output_root, src), profile_dir(output_root, name)
    d.mkdir(parents=True)
    if (s / "modlist.txt").exists():
        shutil.copy2(s / "modlist.txt", d / "modlist.txt")  # the order only; what is enabled is set below
    (d / "profile.json").write_text(json.dumps({"created": datetime.now().astimezone().isoformat(timespec="seconds"),
                                                "init": init}, indent=1), encoding="utf-8")
    print(tr("Створено профіль «{0}»: {1}.", name, tr("моди з колекції") if init == "collection" else tr("усі моди вимкнено, увімкни потрібні")))
    return True

def take_profile_init(pdir: Path) -> str | None:
    """The pending first-time setup of a new profile ("empty"/"collection"), cleared once read."""
    f = pdir / "profile.json"
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return None
    init = data.pop("init", None)
    if init:
        f.write_text(json.dumps(data, indent=1), encoding="utf-8")
    return init

def migrate_collection(output_root: Path) -> None:
    """The collection used to be global; it now belongs to a profile (the v0.9/0.10 one is Default's)."""
    old = output_root / COLLECTION_FILE
    if old.exists():
        d = profile_dir(output_root, DEFAULT_PROFILE)
        d.mkdir(parents=True, exist_ok=True)
        if not (d / COLLECTION_FILE).exists():
            shutil.move(str(old), d / COLLECTION_FILE)
        else:
            old.unlink()

def delete_profile(output_root: Path, mods_root: Path, name: str, active: str) -> None:
    if name not in list_profiles(output_root):
        print(tr("Профілю «{0}» немає.", name))
        return
    if name == active:
        print(tr("Не можу видалити активний профіль «{0}» — спершу перемкнись на інший (--profile).", name))
        return
    j = load_journal(output_root)
    if j and j.get("profile", DEFAULT_PROFILE) == name:
        print(tr("Профіль «{0}» зараз розгорнуто — спершу --undeploy або розгорни інший.", name))
        return
    folders = {p.name for p in mods_root.iterdir() if p.is_dir()}
    raw = {FOMOD_OUT_RE.match(f).group(1) for f in folders if FOMOD_OUT_RE.match(f)} & folders
    mine = set(fomod_owned(profile_dir(output_root, name), raw, folders).values())
    others = fomod_used_elsewhere(output_root, name, raw, folders)
    shutil.rmtree(profile_dir(output_root, name))
    removed = 0
    for f in sorted(mine):
        m = FOMOD_OUT_RE.match(f)
        if m and m.group(2) and f not in others:  # only numbered variants the engine made for profiles
            shutil.rmtree(mods_root / f)
            removed += 1
    print(tr("Профіль «{0}» видалено.", name) + (tr(" Прибрано FOMOD-варіантів, які більше ніхто не використовує: {0}", removed) if removed else ""))

def print_profiles(output_root: Path, active: str) -> None:
    j = load_journal(output_root)
    deployed = j.get("profile", DEFAULT_PROFILE) if j else None
    print(tr("\nПрофілі:"))
    for p in list_profiles(output_root):
        marks = ([tr("активний")] if p == active else []) + ([tr("розгорнуто")] if p == deployed else [])
        print(f"  {p}" + (f"  ({', '.join(marks)})" if marks else ""))

# ===================================================================================
# Mod order (modlist.txt) and file conflicts
# ===================================================================================
MODLIST_HEADER = tr("# Порядок модів для Skyrim Mod Engine.\n# Один мод на рядок. НИЖЧЕ = ВИЩИЙ ПРІОРИТЕТ: його файли перекривають файли модів вище.\n# Можна переставляти рядки вручну; нові моди скрипт вставляє сам з урахуванням master-залежностей.\n# Рядки з # ігноруються.\n") + \
    tr("# «-» на початку рядка (-Назва моду) — мод вимкнено в цьому профілі; приберіть «-», щоб увімкнути.\n")
SEVERITY_ORDER = {tr("критично"): 0, tr("важливо"): 1, tr("помірно"): 2, tr("косметично"): 3}

def conflict_severity(path: str) -> str:
    ext = Path(path).suffix.lower()
    top = path.lower().split("/", 1)[0]
    if ext in PLUGIN_EXTS or ext == ".bsa":
        return tr("критично")
    if ext in (".pex", ".dll", ".swf", ".hkx") or top in ("skse", "scripts"):
        return tr("важливо")
    if ext in (".ini", ".json", ".xml", ".toml", ".psc", ".seq", ".strings", ".dlstrings", ".ilstrings"):
        return tr("помірно")
    return tr("косметично")

def deployable(summaries: list[dict]) -> list[dict]:
    """Mods with a known Data folder, plus mods that only carry game-folder files (Engine Fixes
    preloader, runtime swappers). Raw FOMOD sources are excluded (their [FOMOD] output is used)."""
    return [s for s in summaries
            if (s.get("data_root") is not None and s["layout"] in ("data", "root+data"))
            or (s["layout"] == "root-only" and s.get("root_dir") is not None)]

def data_path(mods_root: Path, s: dict) -> Path | None:
    if s.get("abs_data"):
        return Path(s["abs_data"])
    if s.get("data_root") is None:
        return None
    return mods_root / s["name"] if s["data_root"] == "." else mods_root / s["name"] / s["data_root"]

def mod_dependencies(mods_root: Path, mods: list[dict]) -> dict[str, set[str]]:
    """mod -> mods it depends on (one of its plugins has a master that the other mod provides)."""
    owner: dict[str, str] = {}
    for s in mods:
        for pl in s.get("plugins", []):
            owner.setdefault(pl.lower(), s["name"])
    deps: dict[str, set[str]] = {s["name"]: set() for s in mods}
    for s in mods:
        base = data_path(mods_root, s)
        for pl in s.get("plugins", []):
            for m in read_plugin_masters(base / pl) or []:
                o = owner.get(m.lower())
                if o and o != s["name"]:
                    deps[s["name"]].add(o)
    return deps

def parse_modlist_line(line: str, names: set[str]) -> tuple[str | None, bool]:
    """(mod name, enabled). "-Name" disables Name; a mod whose own name starts with "-" still works."""
    if line in names:
        return line, True
    if line.startswith("-") and line[1:].strip() in names:
        return line[1:].strip(), False
    return None, True

def write_modlist(path: Path, order: list[str], disabled: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(MODLIST_HEADER + "\n".join(("-" + n) if n in disabled else n for n in order) + "\n",
                    encoding="utf-8")

def read_disabled(path: Path, names: set[str]) -> set[str]:
    out = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                n, on = parse_modlist_line(line, names)
                if n and not on:
                    out.add(n)
    return out

def sync_modlist(path: Path, mods: list[dict], deps: dict[str, set[str]],
                 enable_new: set[str] | None = None) -> tuple[list[str], list[str]]:
    """Read modlist.txt, drop vanished mods, insert new ones after their dependencies. Returns (order, notes).
    Disabled mods ("-Name") keep their place in the order; read_disabled() returns them."""
    names = {s["name"] for s in mods}
    order: list[str] = []
    disabled: set[str] = set()
    gone = 0
    existed = path.exists()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            n, on = parse_modlist_line(line, names)
            if n is None:
                gone += 1
            elif n not in order:
                order.append(n)
                if not on:
                    disabled.add(n)
    notes = []
    if gone:
        notes.append(tr("прибрано з порядку модів, яких більше немає: {0}", gone))
    new = sorted((n for n in names if n not in order), key=str.lower)
    # Place new mods whose dependencies are also new after those dependencies.
    pending, placed_new = list(new), []
    while pending:
        ready = [n for n in pending if not (deps[n] & set(pending))] or pending[:1]
        n = ready[0]
        pending.remove(n)
        lo = max((order.index(d) + 1 for d in deps[n] if d in order), default=0)
        dependents = [order.index(m) for m in order if n in deps.get(m, ())]
        hi = min(dependents, default=len(order))
        pos = hi if hi >= lo else len(order)
        order.insert(pos, n)
        placed_new.append(n)
        if existed and enable_new is not None and n not in enable_new:
            disabled.add(n)  # appeared while another profile was active: this profile decides itself
    if placed_new:
        notes.append(tr("додано в порядок: {0}", ', '.join(placed_new)))
    write_modlist(path, order, disabled)
    return order, notes

def order_problems(order: list[str], deps: dict[str, set[str]], disabled: set[str] | None = None) -> list[str]:
    """Problems among enabled mods: a mod above one it depends on, or depending on a disabled mod."""
    disabled = disabled or set()
    pos = {n: i for i, n in enumerate(order) if n not in disabled}
    out = []
    for n in order:
        if n in disabled:
            continue
        for d in sorted(deps.get(n, ())):
            if d in disabled:
                out.append(tr("«{0}» залежить від вимкненого «{1}» — увімкни його або вимкни «{2}»", n, d, n))
            elif d in pos and pos[d] > pos[n]:
                out.append(tr("«{0}» стоїть вище за «{1}», від якого залежать його плагіни — перенеси «{2}» вище в modlist.txt", n, d, d))
    return out

def file_digest(p: Path) -> str:
    h = hashlib.sha1()
    with p.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()

def compute_conflicts(mods_root: Path, index_dir: Path, mods: list[dict], order: list[str]) -> dict:
    pos = {n: i for i, n in enumerate(order)}
    by_name = {s["name"]: s for s in mods}
    providers: dict[str, list[dict]] = {}
    bsa_unreadable = []
    for name in order:
        s = by_name[name]
        base = data_path(mods_root, s)
        idx = index_dir / s["index"]
        if idx.exists():
            with gzip.open(idx, "rt", encoding="utf-8") as f:
                for line in f:
                    rel, _, size = line.rstrip("\n").rpartition("\t")
                    if not rel:
                        continue
                    low = rel.lower()
                    if Path(low).suffix in IGNORED_EXTS or low.startswith(("fomod/", "docs/")) or low == "meta.ini":
                        continue
                    providers.setdefault(low, []).append(
                        {"mod": name, "kind": "loose", "path": rel, "size": int(size or 0), "src": base / rel})
        for b in s.get("bsa", []):
            names = read_bsa_names(base / b)
            if names is None:
                bsa_unreadable.append(f"{name}: {b}")
                continue
            for rel in names:
                providers.setdefault(rel.lower(), []).append({"mod": name, "kind": "bsa", "bsa": b, "path": rel})

    pairs: dict[tuple[str, str], dict] = {}
    files_out, identical, inside_one_mod = [], 0, 0
    for low, provs in providers.items():
        mods_here = {p["mod"] for p in provs}
        if len(mods_here) < 2:
            continue
        loose = [p for p in provs if p["kind"] == "loose"]
        # Loose files always beat BSA contents; among the same kind the later mod in the order wins.
        pool = loose or provs
        winner = max(pool, key=lambda p: pos[p["mod"]])
        losers = [p for p in provs if p["mod"] != winner["mod"]]
        if loose and len({p["mod"] for p in loose}) == len(mods_here) and len({p["size"] for p in loose}) == 1:
            try:
                if len({file_digest(p["src"]) for p in loose}) == 1:
                    identical += 1
                    continue
            except OSError:
                pass
        sev = conflict_severity(winner["path"])
        for l in losers:
            key = (winner["mod"], l["mod"])
            pr = pairs.setdefault(key, {"winner": key[0], "loser": key[1], "count": 0, "by_severity": {},
                                        "examples": [], "loser_in_bsa": 0, "winner_order_independent": 0})
            pr["count"] += 1
            pr["by_severity"][sev] = pr["by_severity"].get(sev, 0) + 1
            if l["kind"] == "bsa":
                pr["loser_in_bsa"] += 1
            if winner["kind"] == "loose" and l["kind"] == "bsa" and pos[l["mod"]] > pos[winner["mod"]]:
                pr["winner_order_independent"] += 1  # BSA mod is lower in the list but still loses
            if len(pr["examples"]) < 200:
                pr["examples"].append((SEVERITY_ORDER[sev], f"[{sev}] {winner['path']}"))
        files_out.append(f"{winner['path']}\t{sev}\t{winner['mod']}\t" + " | ".join(
            f"{l['mod']}" + (f" (BSA {l['bsa']})" if l["kind"] == "bsa" else "") for l in losers))
    for pr in pairs.values():  # most severe examples first
        pr["examples"] = [e for _, e in sorted(pr["examples"])[:8]]
    pair_list = sorted(pairs.values(), key=lambda p: (min(SEVERITY_ORDER[s] for s in p["by_severity"]), -p["count"]))
    return {"pairs": pair_list, "identical": identical, "files": sorted(files_out),
            "bsa_unreadable": bsa_unreadable}

def duplicate_plugins(mods: list[dict]) -> list[tuple[str, list[str]]]:
    seen: dict[str, list[str]] = {}
    for s in mods:
        for pl in s.get("plugins", []):
            seen.setdefault(pl.lower(), []).append(s["name"])
    return [(pl, ms) for pl, ms in sorted(seen.items()) if len(ms) > 1]

def print_conflicts(order: list[str], notes: list[str], problems: list[str], res: dict,
                    dups: list[tuple[str, list[str]]], skipped: list[str]) -> None:
    print(tr("\n=== КОНФЛІКТИ ==="))
    print(tr("Порядок модів (нижче = перемагає; редагується в modlist.txt профілю):"))
    for i, n in enumerate(order, 1):
        print(f"  {i:2}. {n}")
    for n in notes:
        print(f"  ({n})")
    for p in problems:
        print(tr("УВАГА: {0}", p))
    for pl, ms in dups:
        print(tr("КРИТИЧНО: плагін {0} є одразу в: {1} — у грі буде лише один, решта перекриті", pl, ', '.join(ms)))
    if skipped:
        print(tr("Не враховано: {0}", '; '.join(skipped)))
    if not res["pairs"]:
        print(tr("Конфліктів файлів немає."))
    shown = res["pairs"][:15]
    for pr in shown:
        sev = ", ".join(f"{k}: {v}" for k, v in sorted(pr["by_severity"].items(), key=lambda kv: SEVERITY_ORDER[kv[0]]))
        print(tr("\n  «{0}» перекриває «{1}»: {2} файл(ів) ({3})", pr['winner'], pr['loser'], pr['count'], sev))
        if pr["loser_in_bsa"]:
            print(tr("    з них у BSA «{0}»: {1} — loose-файли перемагають BSA незалежно від порядку", pr['loser'], pr['loser_in_bsa']))
        for ex in pr["examples"][:8]:
            print(f"    {ex}")
        if pr["count"] > len(pr["examples"][:8]):
            print(tr("    ... повний список у conflicts.tsv"))
    if len(res["pairs"]) > len(shown):
        print(tr("\n  ...і ще {0} пар(и) менш важливих конфліктів — див. conflicts.json / conflicts.tsv", len(res['pairs']) - len(shown)))
    if res["identical"]:
        print(tr("\n  Однакових файлів у кількох модах (не конфлікт): {0}", res['identical']))
    for b in res["bsa_unreadable"]:
        print(tr("  BSA не вдалося прочитати (вміст не враховано): {0}", b))

# ===================================================================================
# Deployment into the game folder (hard links + backups + journal) and plugins.txt
# ===================================================================================
DEPLOY_JOURNAL = "deployment.json"
# Loose files at the very top of a mod's Data that are documentation, not game data.
NOT_DEPLOYED_TOP = IGNORED_EXTS
NOT_DEPLOYED_NAMES = {"meta.ini", "vortex_override_instructions.json", "__folder_managed_by_vortex"}

def is_manager_metadata(name: str) -> bool:
    """Files mod managers keep next to a mod (Vortex, MO2); the game never reads them."""
    n = name.lower()
    return n in NOT_DEPLOYED_NAMES or (n.startswith("vortex.deployment") and n.endswith(".json"))

GAME_PROCESS_NAMES = {"skyrimse.exe", "skse64_loader.exe"}

def game_running() -> bool:
    """True if the game or the SKSE loader runs (under Proton/Wine the process name is the .exe).
    Only the program itself is checked, not its arguments: a text editor that has a file named
    "skse64_loader.exe" open must not count."""
    for p in Path("/proc").glob("[0-9]*"):
        try:
            argv0 = (p / "cmdline").read_bytes().split(b"\0", 1)[0].decode("utf-8", "replace")
            comm = (p / "comm").read_text(errors="replace").strip().lower()
        except OSError:
            continue
        exe = re.split(r"[\\/]", argv0)[-1].lower()
        if exe in GAME_PROCESS_NAMES or any(n.startswith(comm) and len(comm) >= 12 for n in GAME_PROCESS_NAMES):
            return True
    return False

def plugins_txt_path(game_dir: Path) -> Path | None:
    """Proton keeps %LOCALAPPDATA% inside the compatdata prefix of the same Steam library."""
    base = game_dir.parent.parent / "compatdata" / SKYRIM_APPID / "pfx/drive_c/users/steamuser/AppData/Local"
    if not base.exists():
        return None
    for name in MY_GAMES_NAMES:
        d = ci_join(base, name)
        if d is not None:
            return ci_join(d, "Plugins.txt") or d / "Plugins.txt"
    return base / MY_GAMES_NAMES[0] / "Plugins.txt"

def deploy_plan(mods_root: Path, mods: list[dict], order: list[str]) -> tuple[dict, dict]:
    """(data files, root files): lower-case relative path -> (display path, source, mod). Later mods win."""
    by_name = {s["name"]: s for s in mods}
    data_files: dict[str, tuple[str, Path, str]] = {}
    root_files: dict[str, tuple[str, Path, str]] = {}
    for name in order:
        s = by_name[name]
        base = data_path(mods_root, s)
        rbase = None
        if s.get("root_dir") is not None:
            rbase = mods_root / name if s["root_dir"] == "." else mods_root / name / s["root_dir"]
        for p in (sorted(base.rglob("*")) if base is not None else []):
            if not p.is_file() or p.is_symlink():
                continue
            if rbase is not None and base in rbase.parents and rbase in p.parents:
                continue  # MO2-style Root/ inside the Data folder goes to the game folder, not Data
            rel = p.relative_to(base).as_posix()
            low = rel.lower()
            if "/" not in rel and (p.suffix.lower() in NOT_DEPLOYED_TOP or is_manager_metadata(low)):
                continue
            if low.startswith("fomod/") or ("/" in low and low.split("/", 1)[0] in IGNORED_DIR_NAMES):
                continue
            data_files[low] = (rel, p, name)
        if rbase is not None:
            for p in sorted(rbase.rglob("*")):
                if not p.is_file() or p.is_symlink():
                    continue
                rel = p.relative_to(rbase).as_posix()
                low = rel.lower()
                if "/" not in rel and is_manager_metadata(low):
                    continue
                if base is not None and (base == rbase or rbase in base.parents) and base in p.parents:
                    continue  # SKSE-style: Data/ inside the root folder is deployed above
                if base is not None and "/" in rel and rel.split("/", 1)[0].lower() not in ROOT_DIR_NAMES:
                    # Next to a Data folder (SKSE archive) only top-level files belong to the game folder;
                    # subfolders such as src/ are the source code, not something the game uses.
                    continue
                # Next to SkyrimSE.exe a .txt can be a config some tools read; skip only documentation.
                if "/" not in rel and p.suffix.lower() in IGNORED_EXTS and (
                        p.suffix.lower() not in (".txt", ".md")
                        or re.search(r"readme|changelog|change log|licen[cs]e|credits|install", low)):
                    continue
                root_files[low] = (rel, p, name)
    return data_files, root_files

def load_journal(output_root: Path) -> dict | None:
    j = output_root / DEPLOY_JOURNAL
    if not j.exists():
        return None
    return json.loads(j.read_text(encoding="utf-8"))

def save_journal(output_root: Path, journal: dict) -> None:
    tmp = output_root / (DEPLOY_JOURNAL + ".tmp")
    tmp.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(output_root / DEPLOY_JOURNAL)

def plugin_load_order(mods_root: Path, mods: list[dict], order: list[str]) -> list[tuple[str, Path, list[str]]]:
    """Plugins in mod order, masters (ESM-flagged) first, every plugin after its own masters."""
    by_name = {s["name"]: s for s in mods}
    items = []
    for name in order:
        s = by_name[name]
        base = data_path(mods_root, s)
        for pl in s.get("plugins", []):
            items.append((pl, base / pl, read_plugin_masters(base / pl) or []))
    seen: dict[str, int] = {}
    uniq = []
    for it in items:  # a plugin present in two mods: the later mod's file is the deployed one
        seen[it[0].lower()] = len(uniq)
        uniq.append(it)
    uniq = [it for i, it in enumerate(uniq) if seen[it[0].lower()] == i]

    def is_master_flagged(p: Path) -> bool:
        try:
            with p.open("rb") as f:
                h = f.read(12)
            return len(h) == 12 and h[:4] == b"TES4" and bool(struct.unpack_from("<I", h, 8)[0] & 0x1)
        except OSError:
            return False

    ordered_items = sorted(uniq, key=lambda it: (not (it[0].lower().endswith(".esm") or is_master_flagged(it[1]))))
    names = [it[0].lower() for it in ordered_items]
    result, placed = [], set()
    pending = list(ordered_items)
    while pending:  # stable topological sort: a plugin waits for masters that are also being deployed
        for it in pending:
            need = {m.lower() for m in it[2]} & set(names)
            if need <= placed:
                result.append(it)
                placed.add(it[0].lower())
                pending.remove(it)
                break
        else:  # cycle: keep the rest as is
            result += pending
            break
    return result

FULL_PLUGIN_LIMIT = 254  # 0x00-0xFD; 0xFE is reserved for light (ESL) plugins, up to 4096 of them
LIGHT_PLUGIN_LIMIT = 4096

def is_light_plugin(name: str, path: Path | None) -> bool:
    if name.lower().endswith(".esl"):
        return True
    if path is None:
        return False
    try:
        with path.open("rb") as f:
            h = f.read(12)
        return len(h) == 12 and h[:4] == b"TES4" and bool(struct.unpack_from("<I", h, 8)[0] & 0x200)
    except OSError:
        return False

def plugin_limit_messages(game_dir: Path, active: list[tuple[str, Path | None]]) -> list[str]:
    """Count full vs light plugins the game will load: base masters, Creation Club content, active mods."""
    gdata = ci_join(game_dir, "Data")
    always: list[tuple[str, Path | None]] = [(m, ci_join(gdata, m) if gdata else None) for m in BASE_MASTERS]
    ccc = ci_join(game_dir, "Skyrim.ccc")
    if ccc and gdata:
        for l in ccc.read_text(errors="replace").splitlines():
            if l.strip() and (p := ci_join(gdata, l.strip())):
                always.append((l.strip(), p))
    seen, full, light = set(), 0, 0
    for name, path in always + active:
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        if is_light_plugin(name, path):
            light += 1
        else:
            full += 1
    msgs = []
    if full > FULL_PLUGIN_LIMIT:
        msgs.append(tr("звичайних плагінів {0} > ліміту {1} — гра не запуститься/впаде. Вимкни частину або заміни на ESL-версії (ESL рахуються окремо, до {2})", full, FULL_PLUGIN_LIMIT, LIGHT_PLUGIN_LIMIT))
    if light > LIGHT_PLUGIN_LIMIT:
        msgs.append(tr("ESL-плагінів {0} > ліміту {1}", light, LIGHT_PLUGIN_LIMIT))
    return msgs, full, light

def write_plugins_txt(game_dir: Path, output_root: Path, journal: dict, load: list[tuple[str, Path, list[str]]],
                      dry: bool, disabled: set[str] | None = None) -> list[str]:
    msgs = []
    path = plugins_txt_path(game_dir)
    if path is None:
        msgs.append(tr("Proton-prefix гри не знайдено — запусти гру хоча б раз через Steam; plugins.txt не змінено"))
        return msgs
    original: list[str] = []
    if path.exists():
        original = path.read_text(encoding="utf-8", errors="replace").splitlines()
    ours = {it[0].lower() for it in load}
    # Keep everything the user had that we don't manage, in their order and state.
    kept = [l for l in original if l.strip() and l.lstrip("*").strip().lower() not in ours]
    gdata = ci_join(game_dir, "Data")
    available = set(BASE_MASTERS) | {l.lstrip("*").strip().lower() for l in original if l.strip() and not l.startswith("#")}
    if gdata:
        available |= {p.name.lower() for p in gdata.iterdir() if p.suffix.lower() in PLUGIN_EXTS}
        ccc = ci_join(game_dir, "Skyrim.ccc")
        if ccc:
            available |= {l.strip().lower() for l in ccc.read_text(errors="replace").splitlines() if l.strip()}
    available |= ours
    lines, active = [], []
    for name, ppath, masters in load:
        missing = [m for m in masters if m.lower() not in available]
        if disabled and name.lower() in disabled:
            lines.append(name)  # the collection author left it disabled
        elif missing:
            lines.append(name)  # listed but inactive
            msgs.append(tr("{0}: НЕ активовано — немає master {1} (гра впала б на старті)", name, ', '.join(missing)))
        else:
            lines.append("*" + name)
            active.append((name, ppath))
    gd = ci_join(game_dir, "Data")
    user_active = [(l.lstrip("*").strip(), ci_join(gd, l.lstrip("*").strip()) if gd else None)
                   for l in kept if l.startswith("*")]
    lim, full, light = plugin_limit_messages(game_dir, user_active + active)
    msgs = lim + msgs
    msgs.append(tr("активних плагінів: звичайних {0}/{1}, ESL {2}/{3} (з офіційними та CC)", full, FULL_PLUGIN_LIMIT, light, LIGHT_PLUGIN_LIMIT))
    if dry:
        return msgs
    rec = {"path": str(path)}
    if path.exists():
        bk = Path("backup") / "plugins.txt.original"
        if not (output_root / bk).exists():
            (output_root / bk).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, output_root / bk)
        rec["backup"] = str(bk)
    else:
        rec["created"] = True
        path.parent.mkdir(parents=True, exist_ok=True)
    journal["plugins_txt"] = rec
    # Windows line endings, as the game writes it.
    path.write_text("\r\n".join(["# This file is used by Skyrim to keep track of your downloaded content.",
                                 "# Please do not modify this file."] + [l for l in kept if not l.startswith("#")] + lines) + "\r\n",
                    encoding="utf-8", newline="")
    return msgs

class DirCache:
    """Case-insensitive view of folders, each listed from disk once (and kept in sync with what we create)."""
    def __init__(self):
        self.listing: dict[str, dict[str, str]] = {}

    def children(self, d: Path) -> dict[str, str]:
        key = str(d)
        if key not in self.listing:
            try:
                self.listing[key] = {p.name.lower(): p.name for p in d.iterdir()}
            except OSError:
                self.listing[key] = {}
        return self.listing[key]

    def add(self, d: Path, name: str, is_dir: bool) -> None:
        self.children(d)[name.lower()] = name
        if is_dir:
            self.listing.setdefault(str(d / name), {})

def plan_targets(root: Path, files: dict, cache: DirCache, game_dir: Path,
                 new_dirs: list[str]) -> list[tuple[str, Path, str, bool]]:
    """(target path relative to game dir, source, mod, replaces existing) — computed without touching disk."""
    out = []
    for low, (rel, src, mod) in sorted(files.items()):
        cur = root
        parts = [p for p in rel.split("/") if p]
        for j, part in enumerate(parts):
            if part in (".", ".."):
                raise ValueError(tr("небезпечний шлях: {0}", rel))
            existing = cache.children(cur).get(part.lower())
            if existing is None:
                cache.add(cur, part, is_dir=j < len(parts) - 1)
                if j < len(parts) - 1:
                    new_dirs.append(str((cur / part).relative_to(game_dir)))
                out_name, replaces = part, False
            else:
                out_name, replaces = existing, True
            cur = cur / out_name
        out.append((str(cur.relative_to(game_dir)), src, mod, replaces))
    return out

def order_plugins_with_loot(game_dir: Path, output_root: Path, load: list, data_files: dict,
                            root_files: dict, use_loot: bool, loot_update: bool) -> tuple[list, list[str]]:
    """Apply LOOT rules when the masterlist is available; otherwise keep the engine's own order."""
    if not use_loot:
        return load, [tr("LOOT вимкнено (--no-loot) — порядок за власними правилами")]
    ml, status = fetch_masterlist(output_root, loot_update)
    lines = [status]
    if ml is None:
        return load, lines
    try:
        rules = LootRules(ml)
    except Exception as e:
        return load, lines + [tr("masterlist LOOT не прочитано ({0}) — порядок за власними правилами", e)]
    gdata = ci_join(game_dir, "Data")
    files = set(data_files)
    src_of: dict[str, Path] = {k: v[1] for k, v in data_files.items()}
    if gdata:
        for dp, _, fns in os.walk(gdata):
            rel_dir = os.path.relpath(dp, gdata).replace(os.sep, "/")
            for fn in fns:
                key = (fn if rel_dir == "." else f"{rel_dir}/{fn}").lower()
                files.add(key)
                src_of.setdefault(key, Path(dp) / fn)
    root = {p.name.lower() for p in game_dir.iterdir() if p.is_file()} | set(root_files)
    active = set(BASE_MASTERS) | {it[0].lower() for it in load}
    ccc = ci_join(game_dir, "Skyrim.ccc")
    if ccc:
        active |= {l.strip().lower() for l in ccc.read_text(errors="replace").splitlines() if l.strip()}
    pt = plugins_txt_path(game_dir)
    if pt and pt.exists():
        active |= {l[1:].strip().lower() for l in pt.read_text(errors="replace").splitlines() if l.startswith("*")}

    def master_flag(name: str, path: Path | None) -> bool:
        if name.lower().endswith((".esm", ".esl")):
            return True
        try:
            with path.open("rb") as f:
                h = f.read(12)
            return len(h) == 12 and h[:4] == b"TES4" and bool(struct.unpack_from("<I", h, 8)[0] & 0x1)
        except (OSError, AttributeError):
            return False

    masters = {n for n in active if n.endswith((".esm", ".esl"))} | \
        {it[0].lower() for it in load if master_flag(it[0], it[1])}
    crc_cache: dict[str, int | None] = {}

    def crc_of(rel: str) -> int | None:
        if rel not in crc_cache:
            p = src_of.get(rel)
            if p is None:
                crc_cache[rel] = None
            else:
                c = 0
                with p.open("rb") as f:
                    for block in iter(lambda: f.read(1 << 20), b""):
                        c = zlib.crc32(block, c)
                crc_cache[rel] = c & 0xFFFFFFFF
        return crc_cache[rel]

    world = LootWorld(files, root, active, masters, crc_of)
    ordered, notes, msgs = loot_sort(load, rules, world, lambda it: master_flag(it[0], it[1]))
    (output_root / "loot_report.txt").write_text("\n".join(lines + notes + msgs) + "\n", encoding="utf-8")
    return ordered, lines + notes + [f"LOOT: {m}" for m in msgs]

RUNTIME_CONFIG_EXTS = {".json", ".toml", ".yaml", ".yml"}

def is_runtime_config(data_rel_low: str) -> bool:
    """Data files the game, SKSE plugins or MCM may rewrite while playing. They are deployed as
    copies: a hard link would let the game edit the file in Mods/ itself (shared by all profiles)."""
    suf = PurePosixPath(data_rel_low).suffix
    if suf == ".ini":
        return True
    return suf in RUNTIME_CONFIG_EXTS and data_rel_low.startswith(("skse/plugins/", "mcm/"))

def deploy(game_dir: Path, mods_root: Path, output_root: Path, mods: list[dict], order: list[str],
           dry: bool, use_loot: bool = True, loot_update: bool = False, coll: dict | None = None,
           profile: str = DEFAULT_PROFILE, pdir: Path | None = None) -> bool:
    """Returns False when nothing was deployed (game running, no Data, no space, error)."""
    if game_running():
        print(tr("Гра (або SKSE) зараз запущена — закрий її перед розгортанням."))
        return False
    gdata = ci_join(game_dir, "Data")
    if gdata is None:
        print(tr("У {0} немає папки Data — розгортання скасовано.", game_dir))
        return False
    ow = overwrite_summary(pdir) if pdir is not None else None
    if ow:
        mods, order = mods + [ow], order + [OVERWRITE_NAME]
    data_files, root_files = deploy_plan(mods_root, mods, order)
    print(tr("\n=== РОЗГОРТАННЯ{0} ===", tr(' (пробний прогін, без змін)') if dry else ''))
    print(tr("Гра: {0}", game_dir))
    print(tr("Профіль: {0}", profile))
    for l in check_proton_setup(game_dir, find_srs(game_dir, mods_root)):
        print(l)
    print(tr("Модів: {0}; файлів у Data: {1}; файлів у папку гри: {2}", len(order) - (1 if ow else 0), len(data_files), len(root_files)))
    if ow:
        print(tr("Файли профілю (overwrite): {0}", sum(1 for v in data_files.values() if v[2] == OVERWRITE_NAME)))
    if not dry and load_journal(output_root):
        prev = load_journal(output_root).get("profile", DEFAULT_PROFILE)
        print(tr("Прибираю попереднє розгортання (профіль «{0}»)...", prev) if prev != profile else tr("Прибираю попереднє розгортання..."))
        st = undeploy(game_dir, output_root, quiet=True)
        if st.get("captured"):
            print(tr("  збережено в overwrite профілю «{0}»: {1} файл(ів), змінених чи створених грою", prev, st["captured"]))
        if pdir is not None and not ow:  # the sweep may have just filled this profile's overwrite
            ow = overwrite_summary(pdir)
            if ow:
                mods, order = mods + [ow], order + [OVERWRITE_NAME]
                data_files, root_files = deploy_plan(mods_root, mods, order)
        elif ow:
            data_files, root_files = deploy_plan(mods_root, mods, order)

    cache, new_dirs = DirCache(), []
    plan = plan_targets(gdata, data_files, cache, game_dir, new_dirs) + \
        plan_targets(game_dir, root_files, cache, game_dir, new_dirs)
    replaced = sum(1 for p in plan if p[3])
    gdev = os.stat(game_dir).st_dev
    data_prefix = str(gdata.relative_to(game_dir)) + "/"
    src_stat, cross_n, cross_bytes = [], 0, 0
    for trel, src, mod, _ in plan:
        st = src.stat()
        src_stat.append(st)
        if st.st_dev != gdev:
            cross_n += 1
            cross_bytes += st.st_size
    if cross_n:
        print(tr("УВАГА: моди й гра на різних файлових системах — {0} файл(ів) ({1}) буде скопійовано, а не злінковано (займе місце на диску гри).", cross_n, human_size(cross_bytes)))
        free = shutil.disk_usage(game_dir).free
        if free < cross_bytes + (512 << 20):
            print(tr("Недостатньо місця на диску гри: потрібно {0}, вільно {1}. Розгортання скасовано.", human_size(cross_bytes + (512 << 20)), human_size(free)))
            return False
    load = plugin_load_order(mods_root, mods, order)
    load, loot_lines = order_plugins_with_loot(game_dir, output_root, load, data_files, root_files,
                                              use_loot, loot_update)
    disabled: set[str] = set()
    if coll:
        def _mflag(it):
            if it[0].lower().endswith((".esm", ".esl")):
                return True
            try:
                with it[1].open("rb") as f:
                    h = f.read(12)
                return len(h) == 12 and h[:4] == b"TES4" and bool(struct.unpack_from("<I", h, 8)[0] & 0x1)
            except OSError:
                return False
        load, disabled, cnotes = apply_collection_plugin_order(load, coll, _mflag)
        loot_lines = loot_lines[:1] + cnotes + loot_lines[1:]
    for l in loot_lines[:2] + [x for x in loot_lines[2:] if not x.startswith("LOOT: ")]:
        print(l)
    loot_msgs = [x for x in loot_lines if x.startswith("LOOT: ")]
    for m in loot_msgs[:25]:
        print(f"  {m}"[:220])
    if len(loot_msgs) > 25:
        print(tr("  ...і ще {0} повідомлень LOOT — див. mod_engine_output/loot_report.txt", len(loot_msgs) - 25))
    # Protection of the game's own files, shown on every deployment.
    drift = baseline_drift(game_dir, output_root) if load_baseline(output_root) else []
    if drift:
        print(tr("УВАГА: з часу знімку чистої гри змінилось {0} файл(ів) гри (оновлення Steam?) — перевір: --game-check", len(drift)))
    if incremental_enabled(output_root):
        try:
            n_new, b_new = incremental_backup(game_dir, output_root, [p[0] for p in plan if p[3]], dry)
        except OSError as ex:
            print(tr("ПОМИЛКА інкрементального бекапу: {0}. Розгортання скасовано, гру не змінено.", ex))
            return False
        n_all, b_all = store_stats(output_root)
        if dry:
            n_all, b_all = n_all + n_new, b_all + b_new
        print(tr("Інкрементальний бекап: {0} нових оригіналів ({1}); у сховищі {2} файл(ів), {3}. Повернути: --game-repair",
                 n_new, human_size(b_new), n_all, human_size(b_all)))
    else:
        print(tr("Інкрементальний бекап вимкнено (--incremental-backup on, щоб увімкнути)"))
    if dry:
        msgs = write_plugins_txt(game_dir, output_root, {}, load, True, disabled)
    else:
        # The whole plan goes into the journal BEFORE the first change. Even if the process is killed
        # (sleep, battery, kill -9), --undeploy can tell our files apart: each one is a hard link of
        # its source in Mods/ (same inode), and every original sits at backup/files/<same path>.
        # Files already in Data before deployment; anything else found there at undeploy that is not
        # ours was created by the game or a tool and is saved into the profile's overwrite folder.
        pre_data = sorted(p.relative_to(gdata).as_posix().lower() for p in gdata.rglob("*")
                          if p.is_file() or p.is_symlink())
        journal = {"version": 2, "game_dir": str(game_dir), "profile": profile,
                   "overwrite": str(pdir / "overwrite") if pdir is not None else None,
                   "at": datetime.now().astimezone().isoformat(timespec="seconds"), "order": order,
                   "planned": [], "dirs": new_dirs, "copies": [], "pre_data": pre_data, "complete": False}
        copy_flags = []
        for (trel, src, mod, _), st in zip(plan, src_stat):
            cfg = mod != OVERWRITE_NAME and trel.startswith(data_prefix) and is_runtime_config(trel[len(data_prefix):].lower())
            copy_flags.append(cfg)
            e = {"path": trel, "mod": mod, "dev": st.st_dev, "ino": st.st_ino, "size": st.st_size, "mtime_ns": st.st_mtime_ns}
            if cfg:
                e["copy"] = True
            if cfg or st.st_dev != gdev:
                e["src"] = str(src)  # lets --undeploy recognise a copy cut short by a crash
            journal["planned"].append(e)
        save_journal(output_root, journal)
        backups_root = output_root / "backup" / "files"
        msgs = []
        try:
            for d in new_dirs:
                (game_dir / d).mkdir(exist_ok=True)
            for n_done, ((trel, src, mod, replaces), cfg) in enumerate(zip(plan, copy_flags), 1):
                if n_done % 20000 == 0:
                    print(tr("  розгорнуто {0}/{1} файлів...", n_done, len(plan)), flush=True)
                target = game_dir / trel
                if replaces and (target.exists() or target.is_symlink()):
                    bk = backups_root / trel
                    bk.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(target), bk)
                try:
                    if cfg:
                        raise OSError("runtime config: copy")
                    os.link(src, target)
                except OSError:
                    shutil.copy2(src, target)
                    st = target.stat()
                    journal["copies"].append({"path": trel, "size": st.st_size, "mtime_ns": st.st_mtime_ns})
            msgs = write_plugins_txt(game_dir, output_root, journal, load, False, disabled)
            journal["plugins"] = [it[0] for it in load]
            journal["complete"] = True
        except Exception as e:
            print(tr("ПОМИЛКА розгортання: {0}", e))
            print(tr("План записано в журнал; запусти --undeploy, щоб повністю відкотити."))
            return False
        finally:
            save_journal(output_root, journal)
    print(tr("Наявних файлів гри перекрито (збережено в mod_engine_output/backup): {0}", replaced))
    print(tr("Порядок плагінів ({0}):", len(load)))
    for name, _, _ in load[:40]:
        print(f"  {name}")
    if len(load) > 40:
        print(tr("  ...і ще {0} (повний список у plugins.txt)", len(load) - 40))
    for m in msgs:
        print(m[0].upper() + m[1:] if m.startswith(tr("активних плагінів")) else tr("УВАГА: {0}", m))
    if not dry:
        print(tr("Готово: {0} файлів ({1} жорстких посилань). Відкат: --undeploy", len(plan), len(plan) - len(journal['copies'])))
        if sum(copy_flags):
            print(tr("Конфіги, які гра може змінювати, скопійовано окремо: {0} (зміни збережуться в профілі, а не в Mods/)", sum(copy_flags)))
    srs = find_srs(game_dir if not dry else None, mods_root)
    if srs:
        if (output_root / LAUNCHER_JOURNAL).exists():
            print(tr("\nУВАГА: лаунчер підмінено (--skse-launcher), а SRS патчить SkyrimSELauncher.exe — поверни його: --skse-launcher-restore"))
        print_srs_launch_help(srs)
    return True

def is_prefix_of(part: Path, full: Path) -> bool:
    try:
        with part.open("rb") as a, full.open("rb") as b:
            while True:
                x = a.read(1 << 20)
                if not x:
                    return True
                if b.read(len(x)) != x:
                    return False
    except OSError:
        return False

def undeploy(game_dir: Path, output_root: Path, quiet: bool = False) -> dict:
    """Remove exactly what we deployed, restore originals and plugins.txt. Works after an interrupted run."""
    journal = load_journal(output_root)
    stats = {"removed": 0, "kept_changed": 0, "restored": 0, "missing": 0, "captured": 0}
    if not journal:
        return stats
    gdir = Path(journal["game_dir"])
    kept_dir = output_root / "deploy_changed" / datetime.now().strftime("%Y%m%d-%H%M%S")
    backups_root = output_root / "backup" / "files"

    def keep_aside(p: Path, rel: str) -> None:
        dest = kept_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), dest)
        stats["kept_changed"] += 1

    gdata = ci_join(gdir, "Data")
    data_prefix = (str(gdata.relative_to(gdir)) + "/") if gdata is not None else None
    ow_root = Path(journal["overwrite"]) if journal.get("overwrite") else None

    def capture(p: Path, data_rel: str) -> None:
        """A file the game or a tool changed or created in Data goes to the profile's overwrite."""
        dest = ow_root / data_rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() or dest.is_symlink():
            dest.unlink()
        shutil.move(str(p), dest)
        stats["captured"] += 1
        d = p.parent
        while gdata is not None and d != gdata and gdata in d.parents:
            try:
                d.rmdir()  # folders the game made for it (MCM/Settings...), only when empty
            except OSError:
                break
            d = d.parent

    if "planned" in journal:  # format 2
        copies = {c["path"]: c for c in journal.get("copies", [])}
        for e in journal["planned"]:
            target = gdir / e["path"]
            bk = backups_root / e["path"]
            try:
                st = target.lstat()
            except FileNotFoundError:
                st = None
            if st is not None:
                c = copies.get(e["path"])
                # A copy keeps the source's size and mtime (copy2); allow 2 s for coarse timestamps.
                ours = (st.st_dev, st.st_ino) == (e["dev"], e["ino"]) or \
                    (c is not None and st.st_size == c["size"] and st.st_mtime_ns == c["mtime_ns"]) or \
                    ("size" in e and st.st_size == e["size"] and abs(st.st_mtime_ns - e["mtime_ns"]) <= 2_000_000_000
                     and (e.get("copy") or st.st_dev != e["dev"]))
                if not ours and not journal.get("complete") and e.get("src") and st.st_size <= e.get("size", -1):
                    ours = is_prefix_of(target, Path(e["src"]))  # copy interrupted mid-file
                if ours:
                    target.unlink()
                    stats["removed"] += 1
                elif e.get("copy") and ow_root is not None and data_prefix and e["path"].startswith(data_prefix):
                    capture(target, e["path"][len(data_prefix):])  # a config the game rewrote
                elif bk.exists():
                    keep_aside(target, e["path"])  # replaced after deployment; the original is in backup
                # else: never touched by us (run interrupted before this file) — leave it
            if bk.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(bk), target)
                stats["restored"] += 1
    else:  # format 1 (v0.9 before this fix)
        for f in journal.get("files", []):
            target = gdir / f["path"]
            try:
                st = target.stat()
            except FileNotFoundError:
                stats["missing"] += 1
                continue
            ours = (st.st_dev, st.st_ino) == (f["dev"], f["ino"]) if f.get("link") else \
                (st.st_size == f["size"] and st.st_mtime_ns == f["mtime_ns"])
            if ours:
                target.unlink()
                stats["removed"] += 1
            else:
                keep_aside(target, f["path"])
        for b in reversed(journal.get("backups", [])):
            src = output_root / b["backup"]
            dest = gdir / b["path"]
            if src.exists():
                if dest.exists():
                    keep_aside(dest, b["path"])
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), dest)
                stats["restored"] += 1
    if "pre_data" in journal and ow_root is not None and gdata is not None:
        pre = set(journal["pre_data"])
        for p in sorted(gdata.rglob("*")):
            if (p.is_file() or p.is_symlink()):
                rel = p.relative_to(gdata).as_posix()
                if rel.lower() not in pre:
                    capture(p, rel)
    for d in sorted(journal.get("dirs", []), key=lambda x: -x.count("/")):
        try:
            (gdir / d).rmdir()  # only if empty
        except OSError:
            pass
    pt = journal.get("plugins_txt")
    if pt:
        target = Path(pt["path"])
        if pt.get("backup") and (output_root / pt["backup"]).exists():
            shutil.copy2(output_root / pt["backup"], target)
            # Drop it: the next deployment must back up plugins.txt as it is then, with any manual changes.
            (output_root / pt["backup"]).unlink()
        elif pt.get("created") and target.exists():
            target.unlink()
    (output_root / DEPLOY_JOURNAL).unlink()
    bdir = output_root / "backup"
    if bdir.exists() and not any(p.is_file() for p in bdir.rglob("*")):
        shutil.rmtree(bdir)
    if not quiet and (output_root / LAUNCHER_JOURNAL).exists():
        print(tr("Увага: «Грати» досі запускає SKSE (лаунчер підмінено). Якщо SKSE був із мода і його прибрано, гра не стартує — поверни лаунчер: --skse-launcher-restore"))
    if not quiet:
        print(tr("Відкат: видалено розгорнутих файлів {0}, відновлено оригіналів {1}", stats['removed'], stats['restored'])
              + (tr(", змінених після розгортання відкладено {0} -> {1}", stats['kept_changed'], kept_dir) if stats["kept_changed"] else "")
              + (tr(", уже відсутніх {0}", stats['missing']) if stats["missing"] else "")
              + (tr(", змінених чи створених грою збережено в профіль «{0}» (overwrite): {1}", journal.get("profile", DEFAULT_PROFILE), stats["captured"]) if stats["captured"] else ""))
    return stats

# ===================================================================================
# Minimal YAML reader for LOOT masterlists (stdlib only; PyYAML is used instead when installed).
# Supports what masterlists use: block mappings/sequences, anchors (&), aliases (*), merge keys (<<),
# flow sequences ([...], also across lines), single/double-quoted and multi-line quoted scalars, comments.
# ===================================================================================
class MiniYamlError(ValueError):
    pass

_YAML_BOOL = {"true": True, "yes": True, "on": True, "false": False, "no": False, "off": False}

def _yaml_plain(s: str):
    """Resolve a plain scalar the way YAML 1.1 (PyYAML) does for the types masterlists use."""
    if s in ("", "~", "null", "Null", "NULL"):
        return None
    if s.lower() in _YAML_BOOL and s in (s.lower(), s.capitalize(), s.upper()):
        return _YAML_BOOL[s.lower()]
    t = s.replace("_", "")
    try:
        if re.fullmatch(r"[-+]?0x[0-9a-fA-F]+", t):
            return int(t, 16)
        if re.fullmatch(r"[-+]?0o?[0-7]+", t) and t.lstrip("+-") not in ("0",) and t.lstrip("+-").startswith("0"):
            return int(t.replace("0o", "0").lstrip("+-") or "0", 8) * (-1 if t.startswith("-") else 1)
        if re.fullmatch(r"[-+]?(0|[1-9][0-9]*)", t):
            return int(t)
        if re.fullmatch(r"[-+]?([0-9]+\.[0-9]*|\.[0-9]+)([eE][-+][0-9]+)?", t):
            return float(t)
    except ValueError:
        pass
    return s

def _yaml_unquote(tok: str):
    if tok.startswith("'"):
        return tok[1:-1].replace("''", "'")
    if tok.startswith('"'):
        body, out, i = tok[1:-1], [], 0
        esc = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/", "r": "\r", "0": "\0", " ": " "}
        while i < len(body):
            c = body[i]
            if c == "\\" and i + 1 < len(body):
                n = body[i + 1]
                if n == "x":
                    out.append(chr(int(body[i + 2:i + 4], 16))); i += 4; continue
                if n == "u":
                    out.append(chr(int(body[i + 2:i + 6], 16))); i += 6; continue
                out.append(esc.get(n, n)); i += 2; continue
            out.append(c); i += 1
        return "".join(out)
    return tok

def _yaml_fold(parts: list[str]) -> str:
    """Join the lines of a multi-line quoted scalar: a line break is a space, an empty line a newline."""
    out = parts[0].rstrip()
    blank = 0
    for p in parts[1:]:
        s = p.strip()
        if s == "":
            blank += 1
            continue
        out += ("\n" * blank) if blank else " "
        out += s
        blank = 0
    return out

def _scan_quote_end(s: str, start: int) -> int:
    """Index just past the closing quote of the scalar starting at s[start], or -1 if not closed."""
    q = s[start]
    i = start + 1
    while i < len(s):
        c = s[i]
        if q == "'" and c == "'":
            if i + 1 < len(s) and s[i + 1] == "'":
                i += 2; continue
            return i + 1
        if q == '"' and c == "\\":
            i += 2; continue
        if q == '"' and c == '"':
            return i + 1
        i += 1
    return -1

def _strip_comment(s: str) -> str:
    i, q = 0, None
    while i < len(s):
        c = s[i]
        if q:
            if q == "'" and c == "'" and i + 1 < len(s) and s[i + 1] == "'":
                i += 2; continue
            if q == '"' and c == "\\":
                i += 2; continue
            if c == q:
                q = None
        elif c in "'\"" and (i == 0 or s[i - 1] in " [,:-{"):
            q = c
        elif c == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
        i += 1
    return s.rstrip()

class _MiniYaml:
    def __init__(self, text: str):
        self.anchors: dict[str, object] = {}
        self.lines = self._logical_lines(text)
        self.pos = 0

    def _logical_lines(self, text: str) -> list[tuple[int, str, int]]:
        """(indent, content, line number) with comments removed and multi-line scalars/flows joined."""
        raw = text.replace("\r\n", "\n").split("\n")
        out, i = [], 0
        while i < len(raw):
            line = raw[i]
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or stripped in ("---", "..."):
                i += 1
                continue
            indent = len(line) - len(line.lstrip(" "))
            content = line.strip()
            no = i + 1
            # Block scalar ("key: |", "- &a >-"): collect the more-indented lines, emit a quoted scalar.
            bm = re.search(r"(^|\s|:)([|>])([-+]?)([1-9]?)([-+]?)\s*(#.*)?$", _strip_comment(content) + " ")
            if bm and not re.search(r"['\"]", content[:bm.start(2)].split(":")[-1] if ":" in content[:bm.start(2)] else ""):
                style, chomp = bm.group(2), (bm.group(3) or bm.group(5))
                block, k = [], i + 1
                while k < len(raw) and (raw[k].strip() == "" or len(raw[k]) - len(raw[k].lstrip(" ")) > indent):
                    block.append(raw[k])
                    k += 1
                while block and block[-1].strip() == "":
                    trailing = True
                    block.pop()
                    trailing_count = 1
                nonempty = [b for b in block if b.strip()]
                bi = min(len(b) - len(b.lstrip(" ")) for b in nonempty) if nonempty else 0
                body = [b[bi:] if b.strip() else "" for b in block]
                if style == "|":
                    text = "\n".join(body)
                else:
                    # Folding: two adjacent normal lines join with a space; empty lines become newlines;
                    # more-indented lines keep their line breaks.
                    text, prev = "", None  # prev: None (start), "blank", "normal", "more"
                    for b in body:
                        kind = "blank" if b == "" else ("more" if b[:1] in (" ", "\t") else "normal")
                        if kind == "blank":
                            text += "\n"
                        else:
                            if prev in ("normal",) and kind == "normal":
                                text += " "
                            elif prev in ("normal", "more") and (kind == "more" or prev == "more"):
                                text += "\n"
                            text += b
                        prev = kind if kind != "blank" else ("more" if prev == "more" else "blank")
                tail_blank = k - (i + 1) - len(block)
                if chomp == "-":
                    pass
                elif chomp == "+":
                    text += "\n" * (1 + tail_blank)
                elif text:
                    text += "\n"
                content = _strip_comment(content)[:bm.start(2)].rstrip() + " " + json.dumps(text)
                out.append((indent, content.strip(), no))
                i = k
                continue
            # Multi-line quoted scalar: find the opening quote of the value that is not closed on this line.
            j = 0
            while True:
                m = re.search(r"(^|[\s\[,:-])(['\"])", content[j:])
                if not m:
                    break
                qpos = j + m.start(2)
                if "#" in content[j:qpos] and re.search(r"(^|\s)#", content[j:qpos]):
                    break
                end = _scan_quote_end(content, qpos)
                if end != -1:
                    j = end
                    continue
                parts = [content]
                while end == -1:
                    i += 1
                    if i >= len(raw):
                        raise MiniYamlError(tr("незакриті лапки з рядка {0}", no))
                    parts.append(raw[i])
                    joined = parts[0] + "\n" + "\n".join(parts[1:])
                    end = _scan_quote_end(joined, qpos)
                head, quoted_tail = content[:qpos], None
                folded = _yaml_fold([content[qpos:]] + parts[1:])
                content = head + folded
                j = len(head) + _scan_quote_end(folded, 0) if _scan_quote_end(folded, 0) != -1 else len(content)
            content = _strip_comment(content)
            # Multi-line flow sequence: keep reading until brackets balance.
            while self._bracket_depth(content) > 0:
                i += 1
                if i >= len(raw):
                    raise MiniYamlError(tr("незакрита [ з рядка {0}", no))
                content += " " + _strip_comment(raw[i].strip())
            if content:
                out.append((indent, content, no))
            i += 1
        return out

    @staticmethod
    def _bracket_depth(s: str) -> int:
        depth, i, q = 0, 0, None
        while i < len(s):
            c = s[i]
            if q:
                if q == "'" and c == "'" and i + 1 < len(s) and s[i + 1] == "'":
                    i += 2; continue
                if c == q:
                    q = None
            elif c in "'\"":
                q = c
            elif c == "[":
                depth += 1
            elif c == "]":
                depth -= 1
            i += 1
        return depth

    # ---- values -------------------------------------------------------------------------------
    def _flow(self, s: str, i: int):
        """Parse a flow sequence starting at s[i] == '['. Returns (list, index after ']')."""
        assert s[i] == "["
        i += 1
        items = []
        while True:
            while i < len(s) and s[i] in " ,":
                i += 1
            if i >= len(s):
                raise MiniYamlError(tr("незакрита ["))
            if s[i] == "]":
                return items, i + 1
            if s[i] == "[":
                v, i = self._flow(s, i)
                items.append(v)
                continue
            if s[i] in "'\"":
                end = _scan_quote_end(s, i)
                items.append(_yaml_unquote(s[i:end]))
                i = end
                continue
            j = i
            while j < len(s) and s[j] not in ",]":
                j += 1
            items.append(self._scalar_or_alias(s[i:j].strip()))
            i = j

    def _scalar_or_alias(self, tok: str):
        if tok.startswith("*"):
            name = tok[1:].strip()
            if name not in self.anchors:
                raise MiniYamlError(tr("невідомий якір *{0}", name))
            return self.anchors[name]
        if tok[:1] in ("'", '"'):
            return _yaml_unquote(tok)
        if tok.startswith("["):
            v, _ = self._flow(tok, 0)
            return v
        return _yaml_plain(tok)

    def _inline_value(self, s: str):
        """Value written on the same line. Returns (value, anchor or None, needs_block)."""
        anchor = None
        s = s.strip()
        if s.startswith("&"):
            parts = s.split(None, 1)
            anchor = parts[0][1:]
            s = parts[1] if len(parts) > 1 else ""
        if s == "":
            return None, anchor, True
        return self._scalar_or_alias(s), anchor, False

    # ---- blocks -------------------------------------------------------------------------------
    def parse(self):
        if not self.lines:
            return None
        return self._block(self.lines[0][0])

    def _block(self, indent: int):
        _, content, _ = self.lines[self.pos]
        if content.startswith("- ") or content == "-":
            return self._seq(indent)
        if self._key_split(content) is None:
            # A scalar written on its own line (e.g. after "- &anchor"); plain scalars may continue
            # on following, more indented lines and are folded with spaces.
            self.pos += 1
            parts = [content]
            while self.pos < len(self.lines) and self.lines[self.pos][0] > indent \
                    and not content[:1] in ("'", '"', "[", "*"):
                parts.append(self.lines[self.pos][1])
                self.pos += 1
            return self._scalar_or_alias(" ".join(parts))
        return self._map(indent)

    def _key_split(self, content: str):
        if content[:1] in ("'", '"'):
            end = _scan_quote_end(content, 0)
            key = _yaml_unquote(content[:end])
            rest = content[end:].lstrip()
            if not rest.startswith(":"):
                return None
            return key, rest[1:]
        m = re.match(r"([^:#\[\]{},]+?|<<)\s*:(\s|$)", content)
        if not m:
            return None
        return m.group(1).strip(), content[m.end(0):]

    def _child(self, parent_indent: int, allow_same_indent_seq: bool):
        """Nested block after 'key:' (deeper indent, or a '- ' list at the same indent)."""
        if self.pos < len(self.lines):
            ind, content, _ = self.lines[self.pos]
            if ind > parent_indent or (allow_same_indent_seq and ind == parent_indent and content.startswith("- ")):
                return self._block(ind)
        return None

    def _map(self, indent: int, first: tuple | None = None) -> dict:
        result: dict = {}
        merges: list[dict] = []
        while self.pos < len(self.lines):
            ind, content, no = self.lines[self.pos]
            if first is not None:
                ind, content = first
                first = None
            elif ind != indent or content.startswith("- "):
                if ind > indent:
                    raise MiniYamlError(tr("неочікуваний відступ у рядку {0}", no))
                break
            ks = self._key_split(content)
            if ks is None:
                raise MiniYamlError(tr("очікувався ключ у рядку {0}: {1}", no, content[:60]))
            key, rest = ks
            self.pos += 1
            value, anchor, needs_block = self._inline_value(rest)
            if needs_block:
                value = self._child(indent, allow_same_indent_seq=True)
            if anchor:
                self.anchors[anchor] = value
            if key == "<<":
                merges.extend(value if isinstance(value, list) else [value])
            else:
                result[key] = value
        for m in merges:  # explicit keys win; earlier merge sources win over later ones
            if isinstance(m, dict):
                for k, v in m.items():
                    if k not in result:
                        result[k] = v
        return result

    def _seq(self, indent: int) -> list:
        items = []
        while self.pos < len(self.lines):
            ind, content, no = self.lines[self.pos]
            if ind != indent or not (content.startswith("- ") or content == "-"):
                break
            body = content[1:].lstrip()
            item_indent = ind + (len(content) - len(body))
            anchor = None
            if body.startswith("&"):
                parts = body.split(None, 1)
                anchor = parts[0][1:]
                body = parts[1] if len(parts) > 1 else ""
            if body == "":
                self.pos += 1
                value = self._child(indent, allow_same_indent_seq=False)
            elif self._key_split(body) is not None and not body.startswith(("*", "[")):
                # "- key: value" starts a mapping whose keys are aligned with "key"
                value = self._map(item_indent, first=(item_indent, body))
            elif body.startswith("- "):
                self.lines[self.pos] = (item_indent, body, no)
                value = self._seq(item_indent)
            else:
                self.pos += 1
                value = self._scalar_or_alias(body)
            if anchor:
                self.anchors[anchor] = value
            items.append(value)
        return items

def yaml_load(text: str):
    try:
        import yaml  # PyYAML, when present, is the reference implementation
        return yaml.safe_load(text)
    except ImportError:
        return _MiniYaml(text).parse()

# ===================================================================================
# LOOT masterlist: plugin sorting rules, groups, dirty/incompatible/missing warnings
# ===================================================================================
# The masterlist is LOOT's public rule database (github.com/loot/skyrimse). With it the plugin order
# follows LOOT's "after" rules and groups; without it (no internet and no cache) the engine falls back
# to its own order: masters first, every plugin after its masters, then mod order.
LOOT_BRANCHES = ("v0.33", "v0.32", "v0.31", "v0.30", "v0.29", "v0.26", "v0.21")
LOOT_URL = "https://raw.githubusercontent.com/loot/skyrimse/{}/masterlist.yaml"
LOOT_MAX_AGE_DAYS = 7
LOOT_UNKNOWN = object()  # condition that cannot be evaluated here (version checks etc.)

def fetch_masterlist(output_root: Path, force: bool) -> tuple[Path | None, str]:
    """Cached masterlist path and a status line. Downloads when missing, stale or forced."""
    import urllib.request
    d = output_root / "loot"
    d.mkdir(parents=True, exist_ok=True)
    cache, meta_p = d / "masterlist.yaml", d / "masterlist.json"
    meta = json.loads(meta_p.read_text(encoding="utf-8")) if meta_p.exists() else {}
    age_days = (datetime.now().timestamp() - meta.get("fetched", 0)) / 86400
    if cache.exists() and not force and age_days < LOOT_MAX_AGE_DAYS:
        return cache, tr("masterlist LOOT {0} з кешу ({1:.0f} дн.)", meta.get('branch', '?'), age_days)
    for br in LOOT_BRANCHES:
        try:
            with urllib.request.urlopen(LOOT_URL.format(br), timeout=20) as r:
                data = r.read()
        except Exception:
            continue
        if b"plugins:" not in data:
            continue
        tmp = cache.with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(cache)
        meta_p.write_text(json.dumps({"branch": br, "fetched": datetime.now().timestamp()}), encoding="utf-8")
        return cache, tr("masterlist LOOT {0} завантажено", br)
    if cache.exists():
        return cache, tr("masterlist LOOT не оновлено (немає зв'язку) — використовую кеш {0} ({1:.0f} дн.)", meta.get('branch', '?'), age_days)
    return None, tr("masterlist LOOT недоступний (немає інтернету й кешу) — порядок за власними правилами")

class LootWorld:
    """What conditions can see: files in Data (after deployment), active plugins, master flags."""
    def __init__(self, files: set[str], root_files: set[str], active: set[str], masters: set[str],
                 crc_of):
        self.files = files            # lower-case paths relative to Data
        self.root_files = root_files  # lower-case paths relative to the game folder
        self.active = active          # lower-case plugin names
        self.masters = masters        # lower-case names of master-flagged plugins
        self.crc_of = crc_of          # callable(path) -> int | None
        self._rx_cache: dict[str, re.Pattern] = {}

    def _rx(self, pattern: str) -> re.Pattern:
        if pattern not in self._rx_cache:
            self._rx_cache[pattern] = re.compile(pattern, re.IGNORECASE)
        return self._rx_cache[pattern]

    def _match_files(self, path: str, pool: set[str] | None = None) -> list[str]:
        path = path.replace("\\", "/")
        if path.startswith("../"):
            pool, path = self.root_files, path[3:]
        pool = self.files if pool is None else pool
        folder, _, name = path.rpartition("/")
        if re.search(r"[:\\*?|\[\]()^$+{}]", name):  # regex in the file name part (LOOT rule)
            rx = self._rx(name)
            pre = (folder.lower() + "/") if folder else ""
            return [f for f in pool if f.startswith(pre) and "/" not in f[len(pre):] and rx.fullmatch(f[len(pre):])]
        return [path.lower()] if path.lower() in pool else []

    def file(self, p: str) -> bool:
        return bool(self._match_files(p))

    def active_(self, p: str) -> bool:
        if re.search(r"[:\\*?|\[\]()^$+{}]", p):
            rx = self._rx(p)
            return any(rx.fullmatch(a) for a in self.active)
        return p.lower() in self.active

    def many(self, p: str) -> bool:
        return len(self._match_files(p)) > 1

    def many_active(self, p: str) -> bool:
        rx = self._rx(p)
        return sum(1 for a in self.active if rx.fullmatch(a)) > 1

    def is_master(self, p: str) -> bool:
        return p.lower() in self.masters

    def checksum(self, p: str, crc_hex: str):
        hits = self._match_files(p)
        if not hits:
            return False
        c = self.crc_of(p.replace("\\", "/").lower())
        return LOOT_UNKNOWN if c is None else c == int(crc_hex, 16)

def eval_loot_condition(cond: str, w: LootWorld):
    """Evaluate a LOOT condition string. Returns True/False, or LOOT_UNKNOWN if it uses functions
    that cannot be checked here (version, product_version, ...)."""
    toks = re.findall(r'\(|\)|"(?:[^"\\]|\\.)*"|[A-Za-z0-9_.]+|,|[<>=!]=?', cond)
    pos = 0

    def peek():
        return toks[pos] if pos < len(toks) else None

    def take():
        nonlocal pos
        pos += 1
        return toks[pos - 1]

    def expr():
        v = term()
        while peek() == "or":
            take()
            r = term()
            v = True if (v is True or r is True) else (LOOT_UNKNOWN if LOOT_UNKNOWN in (v, r) else False)
        return v

    def term():
        v = factor()
        while peek() == "and":
            take()
            r = factor()
            v = False if (v is False or r is False) else (LOOT_UNKNOWN if LOOT_UNKNOWN in (v, r) else True)
        return v

    def factor():
        t = take()
        if t == "not":
            v = factor()
            return LOOT_UNKNOWN if v is LOOT_UNKNOWN else (not v)
        if t == "(":
            v = expr()
            take()  # ")"
            return v
        fn = t
        take()  # "("
        args = []
        while peek() != ")":
            a = take()
            if a == ",":
                continue
            args.append(a[1:-1].replace('\\"', '"').replace("\\\\", "\\") if a.startswith('"') else a)
        take()  # ")"
        if fn == "file":
            return w.file(args[0])
        if fn == "readable":
            return w.file(args[0])
        if fn == "active":
            return w.active_(args[0])
        if fn == "many":
            return w.many(args[0])
        if fn == "many_active":
            return w.many_active(args[0])
        if fn == "is_master":
            return w.is_master(args[0])
        if fn == "checksum":
            return w.checksum(args[0], args[1])
        return LOOT_UNKNOWN  # version, product_version, is_executable, filename_version, ...

    try:
        return expr()
    except Exception:
        return LOOT_UNKNOWN

def loot_text(content, subs=None) -> str:
    """Message content (string or localized list) -> plain English text with {0} substitutions."""
    if isinstance(content, list):
        content = next((c.get("text", "") for c in content if c.get("lang") == "en"),
                       content[0].get("text", "") if content else "")
    text = str(content or "")
    for i, s in enumerate(subs or []):
        text = text.replace("{%d}" % i, str(s))
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1 (\2)", text)  # markdown links
    return text.replace("**", "").strip()

class LootRules:
    def __init__(self, path: Path):
        data = yaml_load(path.read_text(encoding="utf-8")) or {}
        self.groups = data.get("groups") or []
        self.exact: dict[str, list[dict]] = {}
        self.regex: list[tuple[re.Pattern, dict]] = []
        for e in data.get("plugins") or []:
            name = str(e.get("name", ""))
            if re.search(r"[:\\*?|]", name):  # LOOT treats such names as regular expressions
                try:
                    self.regex.append((re.compile(name, re.IGNORECASE), e))
                except re.error:
                    pass
            else:
                self.exact.setdefault(name.lower(), []).append(e)
        self.group_rank = self._rank_groups()

    def _rank_groups(self) -> dict[str, int]:
        after = {g["name"]: list(g.get("after") or []) for g in self.groups}
        after.setdefault("default", [])
        rank: dict[str, int] = {}
        visiting: set[str] = set()

        def depth(g: str) -> int:
            if g in rank:
                return rank[g]
            if g in visiting:
                return 0
            visiting.add(g)
            r = 1 + max((depth(a) for a in after.get(g, []) if a in after), default=-1)
            visiting.discard(g)
            rank[g] = r
            return r

        for g in after:
            depth(g)
        return rank

    def entries(self, plugin: str) -> list[dict]:
        out = list(self.exact.get(plugin.lower(), []))
        out += [e for rx, e in self.regex if rx.fullmatch(plugin)]
        return out

def loot_sort(load: list[tuple[str, Path, list[str]]], rules: LootRules, w: LootWorld,
              is_master_fn) -> tuple[list[tuple[str, Path, list[str]]], list[str], list[str]]:
    """Order plugins: hard rules (masters, ESM flag), LOOT "after" rules, LOOT groups, then the
    incoming order. Returns (ordered plugins, applied notes, warnings/messages)."""
    names = [it[0] for it in load]
    low = {n.lower(): i for i, n in enumerate(names)}
    n = len(load)
    succ: list[set[int]] = [set() for _ in range(n)]
    notes, msgs = [], []
    master_flag = [is_master_fn(it) for it in load]

    def reachable(a: int, b: int) -> bool:
        stack, seen = [a], {a}
        while stack:
            x = stack.pop()
            if x == b:
                return True
            for y in succ[x]:
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        return False

    # 1) every plugin after its masters (the game requires it)
    for i, (_, _, masters) in enumerate(load):
        for m in masters:
            j = low.get(m.lower())
            if j is not None and j != i:
                succ[j].add(i)
    # 2) LOOT "after" rules, skipped if they would contradict the hard rules
    groups, applied, skipped = [], 0, 0
    for i, (name, path, _) in enumerate(load):
        ents = rules.entries(name)
        grp = next((e["group"] for e in ents if e.get("group")), "default")
        groups.append(rules.group_rank.get(grp, rules.group_rank.get("default", 0)))
        for e in ents:
            for a in e.get("after") or []:
                aname, cond = (a, None) if not isinstance(a, dict) else (a.get("name", ""), a.get("condition"))
                j = low.get(str(aname).lower())
                if j is None or j == i:
                    continue
                if cond:
                    c = eval_loot_condition(cond, w)
                    if c is not True:
                        continue
                if master_flag[j] is False and master_flag[i] is True:
                    skipped += 1  # an ESM cannot load after an ESP
                    continue
                if reachable(i, j):
                    skipped += 1
                    continue
                succ[j].add(i)
                applied += 1
    if applied or skipped:
        notes.append(tr("правил LOOT «після»: застосовано {0}", applied) + (tr(", пропущено суперечливих {0}", skipped) if skipped else ""))
    # 3) Kahn with priority: masters first, then LOOT group, then incoming (mod) order
    indeg = [0] * n
    for i in range(n):
        for j in succ[i]:
            indeg[j] += 1
    import heapq
    heap = [((not master_flag[i]), groups[i], i) for i in range(n) if indeg[i] == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        _, _, i = heapq.heappop(heap)
        order.append(i)
        for j in succ[i]:
            indeg[j] -= 1
            if indeg[j] == 0:
                heapq.heappush(heap, ((not master_flag[j]), groups[j], j))
    if len(order) < n:  # a cycle among masters (broken mods): keep the rest in incoming order
        order += [i for i in range(n) if i not in set(order)]
    named_groups = sum(1 for i in range(n) if any(e.get("group") for e in rules.entries(load[i][0])))
    if named_groups:
        notes.append(tr("плагінів із групою LOOT: {0}", named_groups))
    # 4) messages: dirty plugins, incompatibilities, missing requirements, warnings/errors
    for i in order:
        name, path, _ = load[i]
        ents = rules.entries(name)
        if not ents:
            continue
        crc = None
        for e in ents:
            for dty in e.get("dirty") or []:
                if crc is None:
                    crc = w.crc_of(name.lower())
                if crc is not None and dty.get("crc") == crc:
                    bits = ", ".join(f"{k.upper()} {dty[k]}" for k in ("itm", "udr", "nav") if dty.get(k))
                    msgs.append(tr("{0}: «брудний» ({1}) — почисти в SSEEdit", name, bits))
            for inc in e.get("inc") or []:
                iname, cond = (inc, None) if not isinstance(inc, dict) else (inc.get("name", ""), inc.get("condition"))
                if cond and eval_loot_condition(cond, w) is not True:
                    continue
                present = w.active_(iname) if Path(str(iname)).suffix.lower() in PLUGIN_EXTS else w.file(iname)
                if present:
                    label = inc.get("display") if isinstance(inc, dict) and inc.get("display") else iname
                    msgs.append(tr("{0}: несумісний з {1}", name, loot_text(label)))
            for req in e.get("req") or []:
                rname, cond = (req, None) if not isinstance(req, dict) else (req.get("name", ""), req.get("condition"))
                if cond and eval_loot_condition(cond, w) is not True:
                    continue
                present = w.active_(rname) if Path(str(rname)).suffix.lower() in PLUGIN_EXTS else w.file(rname)
                if not present:
                    label = req.get("display") if isinstance(req, dict) and req.get("display") else rname
                    msgs.append(tr("{0}: потрібен {1}", name, loot_text(label)))
            for m in e.get("msg") or []:
                if not isinstance(m, dict) or m.get("type") not in ("warn", "error"):
                    continue
                if m.get("condition") and eval_loot_condition(m["condition"], w) is not True:
                    continue
                msgs.append(f"{name}: {loot_text(m.get('content'), m.get('subs'))}")
    return [load[i] for i in order], notes, msgs

# ===================================================================================
# Vortex / Nexus collections (collection.json): archive matching, author's FOMOD choices,
# mod order rules and plugin load order
# ===================================================================================
COLLECTION_FILE = "collection.json"
NEXUS_FILE_URL = "https://www.nexusmods.com/{domain}/mods/{mod}?tab=files&file_id={file}"

def load_collection(pdir: Path, arg: str | None) -> dict | None:
    """--collection PATH stores a copy that is used on later runs; --collection none stops using it."""
    dest = pdir / COLLECTION_FILE
    if arg:
        if arg.strip().lower() == "none":
            if dest.exists():
                dest.unlink()
            print(tr("Колекцію відключено."))
            return None
        src = Path(arg).expanduser()
        if not src.is_file():
            print(tr("Файл колекції не знайдено: {0}", src))
            return None
        if src.resolve() != dest.resolve():
            shutil.copy2(src, dest)
    if not dest.exists():
        return None
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
    except Exception as e:
        print(tr("collection.json не прочитано: {0}", e))
        return None
    if not isinstance(data.get("mods"), list):
        print(tr("collection.json не схожий на колекцію Vortex (немає списку mods)"))
        return None
    return data

def md5_file(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()

def match_collection(coll: dict, archives_root: Path, output_root: Path) -> dict[int, str]:
    """collection mod index -> archive path (relative to mods-archives), matched by MD5.
    Only archives whose size equals some collection file are hashed; results are cached."""
    cache_p = output_root / "md5_cache.json"
    cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
    by_size: dict[int, list[int]] = {}
    by_md5: dict[str, list[int]] = {}
    for i, m in enumerate(coll["mods"]):
        s = m.get("source") or {}
        if s.get("fileSize"):
            by_size.setdefault(int(s["fileSize"]), []).append(i)
        if s.get("md5"):
            by_md5.setdefault(s["md5"].lower(), []).append(i)
    archives = sorted(p for p in archives_root.rglob("*") if p.is_file() and is_archive(p))
    todo = [p for p in archives if p.stat().st_size in by_size]
    match: dict[int, str] = {}
    hashed = 0
    for n, p in enumerate(todo, 1):
        rel = str(p.relative_to(archives_root))
        st = p.stat()
        c = cache.get(rel)
        if not c or c.get("size") != st.st_size or c.get("mtime_ns") != st.st_mtime_ns:
            c = {"size": st.st_size, "mtime_ns": st.st_mtime_ns, "md5": md5_file(p)}
            cache[rel] = c
            hashed += 1
            if hashed % 50 == 0:
                print(tr("  MD5: {0}/{1} архівів...", n, len(todo)))
                cache_p.write_text(json.dumps(cache), encoding="utf-8")
        for i in by_md5.get(c["md5"], []):
            match.setdefault(i, rel)
    cache_p.write_text(json.dumps(cache), encoding="utf-8")
    return match

def collection_report(coll: dict, match: dict[int, str], archives_root: Path, output_root: Path,
                      game_version: str | None) -> None:
    mods = coll["mods"]
    info = coll.get("info") or {}
    print(tr("\n=== КОЛЕКЦІЯ: {0} (автор {1}) ===", info.get('name', '?'), info.get('author', '?')))
    gv = info.get("gameVersions") or []
    if gv and game_version:
        if any(game_version.startswith(v.rstrip(".0")) or v.startswith(game_version) for v in gv):
            print(tr("Версія гри {0}: збігається з колекцією ({1})", game_version, ', '.join(gv)))
        else:
            print(tr("УВАГА: колекція зібрана під {0}, а гра {1}", ', '.join(gv), game_version))
    req = [i for i, m in enumerate(mods) if not m.get("optional")]
    opt = [i for i, m in enumerate(mods) if m.get("optional")]
    miss_req = [i for i in req if i not in match]
    miss_opt = [i for i in opt if i not in match]
    print(tr("Архівів знайдено: обов'язкових {0}/{1}, необов'язкових {2}/{3}", len(req) - len(miss_req), len(req), len(opt) - len(miss_opt), len(opt)))
    lines = []
    for title, idx in ((tr("ВІДСУТНІ ОБОВ'ЯЗКОВІ"), miss_req), (tr("відсутні необов'язкові"), miss_opt)):
        if not idx:
            continue
        lines.append(f"{title} ({len(idx)}):")
        for i in idx:
            m, s = mods[i], mods[i].get("source") or {}
            url = NEXUS_FILE_URL.format(domain=m.get("domainName", "skyrimspecialedition"),
                                        mod=s.get("modId"), file=s.get("fileId"))
            lines.append(f"  {m.get('name')} v{m.get('version')} — {url}")
    used = set(match.values())
    extra = [str(p.relative_to(archives_root)) for p in sorted(archives_root.rglob("*"))
             if p.is_file() and is_archive(p) and str(p.relative_to(archives_root)) not in used]
    if extra:
        lines.append(tr("Архіви, яких немає в колекції ({0}) — інші версії чи зайві файли:", len(extra)))
        lines += [f"  {e}" for e in extra]
    (output_root / "collection_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    shown = 0
    for l in lines:
        if shown >= 25:
            print(tr("  ...повний список у mod_engine_output/collection_report.txt"))
            break
        print(l)
        shown += 1

def collection_mod_names(coll: dict, match: dict[int, str], state: dict) -> dict[int, str]:
    """collection mod index -> mod folder name in Mods/ (for extracted archives)."""
    out = {}
    for i, rel in match.items():
        dest = (state.get(rel) or {}).get("destination")
        if dest:
            out[i] = Path(dest).name
    return out

def collection_fomod_choices(coll: dict, idx_to_mod: dict[int, str]) -> dict[str, list]:
    """Mod folder name -> author's FOMOD choices (Vortex format: list of visited steps)."""
    out = {}
    for i, name in idx_to_mod.items():
        ch = coll["mods"][i].get("choices")
        if isinstance(ch, dict) and ch.get("type") == "fomod":
            out[name] = ch.get("options") or []
    return out

def _resolve_ref(ref: dict, md5_to_idx: dict[str, int], lfn_to_idx: dict[str, int]) -> int | None:
    if ref.get("fileMD5") and ref["fileMD5"].lower() in md5_to_idx:
        return md5_to_idx[ref["fileMD5"].lower()]
    lfn = (ref.get("logicalFileName") or "").lower()
    return lfn_to_idx.get(lfn) if lfn else None

def apply_collection_mod_rules(coll: dict, idx_to_mod: dict[int, str], order: list[str],
                               deps: dict[str, set[str]]) -> tuple[list[str], list[str]]:
    """Reorder mods so the author's before/after rules hold (master dependencies stay hard).
    Ties keep the current order. Rules that would create a cycle are skipped and reported."""
    mods = coll["mods"]
    md5_to_idx = {(m.get("source") or {}).get("md5", "").lower(): i for i, m in enumerate(mods)}
    lfn_to_idx = {(m.get("source") or {}).get("logicalFilename", "").lower(): i for i, m in enumerate(mods)}
    names = set(order)

    def folder(i: int | None) -> str | None:
        if i is None or i not in idx_to_mod:
            return None
        n = idx_to_mod[i]
        for x in names:  # this profile's installed FOMOD folder ("[FOMOD]" or a "[FOMOD N]" variant)
            m = FOMOD_OUT_RE.match(x)
            if m and m.group(1) == n:
                return x
        return n if n in names else None

    pos = {n: k for k, n in enumerate(order)}
    succ: dict[str, set[str]] = {n: set() for n in order}

    def reachable(a: str, b: str) -> bool:
        stack, seen = [a], {a}
        while stack:
            x = stack.pop()
            if x == b:
                return True
            for y in succ[x]:
                if y not in seen:
                    seen.add(y)
                    stack.append(y)
        return False

    for n in order:  # master dependencies first: they are hard requirements
        for d in deps.get(n, ()):
            if d in succ and not reachable(n, d):
                succ[d].add(n)
    applied = skipped = unresolved = 0
    conflicts = []
    for r in coll.get("modRules") or []:
        a = folder(_resolve_ref(r.get("source") or {}, md5_to_idx, lfn_to_idx))
        b = folder(_resolve_ref(r.get("reference") or {}, md5_to_idx, lfn_to_idx))
        if r.get("type") == "conflicts":
            if a and b:
                conflicts.append(tr("автор позначив як несумісні: «{0}» і «{1}»", a, b))
            continue
        if not a or not b or a == b:
            unresolved += 1
            continue
        first, then = (a, b) if r.get("type") == "before" else (b, a) if r.get("type") == "after" else (None, None)
        if first is None:
            continue
        if reachable(then, first):
            skipped += 1
            continue
        succ[first].add(then)
        applied += 1
    import heapq
    indeg = {n: 0 for n in order}
    for n in order:
        for m in succ[n]:
            indeg[m] += 1
    heap = [(pos[n], n) for n in order if indeg[n] == 0]
    heapq.heapify(heap)
    out = []
    while heap:
        _, n = heapq.heappop(heap)
        out.append(n)
        for m in succ[n]:
            indeg[m] -= 1
            if indeg[m] == 0:
                heapq.heappush(heap, (pos[m], m))
    out += [n for n in order if n not in set(out)]
    notes = [tr("правил порядку модів з колекції: застосовано {0}", applied)
             + (tr(", пропущено суперечливих {0}", skipped) if skipped else "")
             + (tr(", стосуються модів, яких немає: {0}", unresolved) if unresolved else "")]
    return out, notes + conflicts

def apply_collection_plugin_order(load: list, coll: dict, is_master_fn) -> tuple[list, set[str], list[str]]:
    """Author's load order for listed plugins; unlisted ones keep their place relative to their
    predecessor; masters still load before dependents. Returns (order, disabled names, notes)."""
    listed = [(p.get("name") or "").lower() for p in coll.get("plugins") or []]
    if not listed:
        return load, set(), []
    cidx = {n: k for k, n in enumerate(listed)}
    disabled = {(p.get("name") or "").lower() for p in coll.get("plugins") or [] if p.get("enabled") is False}
    key, last = [], -1.0
    for k, it in enumerate(load):
        c = cidx.get(it[0].lower())
        if c is not None:
            last = float(c)
            key.append(last)
        else:
            last += 0.001
            key.append(last)
    low = {it[0].lower(): k for k, it in enumerate(load)}
    succ = [set() for _ in load]
    for k, (_, _, masters) in enumerate(load):
        for m in masters:
            j = low.get(m.lower())
            if j is not None and j != k:
                succ[j].add(k)
    for rule in (coll.get("pluginRules") or {}).get("plugins") or []:
        k = low.get((rule.get("name") or "").lower())
        for a in rule.get("after") or []:
            j = low.get(str(a).lower())
            if k is not None and j is not None and j != k:
                succ[j].add(k)
    import heapq
    indeg = [0] * len(load)
    for k in range(len(load)):
        for j in succ[k]:
            indeg[j] += 1
    flag = [bool(is_master_fn(it)) for it in load]
    heap = [((not flag[k]), key[k], k) for k in range(len(load)) if indeg[k] == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        _, _, k = heapq.heappop(heap)
        order.append(k)
        for j in succ[k]:
            indeg[j] -= 1
            if indeg[j] == 0:
                heapq.heappush(heap, ((not flag[j]), key[j], j))
    order += [k for k in range(len(load)) if k not in set(order)]
    in_coll = sum(1 for it in load if it[0].lower() in cidx)
    notes = [tr("порядок плагінів автора колекції: {0} з {1} плагінів", in_coll, len(load))
             + (tr("; поза колекцією {0} — розставлено LOOT/власними правилами", len(load) - in_coll) if in_coll < len(load) else "")]
    return [load[k] for k in order], disabled, notes

# ===================================================================================
# Skyrim Runtime Swapper (SRS, "Best of Both Worlds"): the game stays 1.7.x on disk; when
# skse64_loader.exe starts, SRS's version.dll patches SkyrimSE.exe / SkyrimSELauncher.exe /
# Skyrim - Shaders.bsa to the target runtime (e.g. 1.6.1170) before SKSE checks the version.
# ===================================================================================
SKSE_LAUNCH_OPTIONS = ("WINEDLLOVERRIDES=\"version=n,b\" "
                       "bash -c 'exec \"${@/SkyrimSELauncher.exe/skse64_loader.exe}\"' -- %command%")

def find_srs(game_dir: Path | None, mods_root: Path | None = None) -> dict | None:
    """SRS manifest from the game folder (deployed) or from an extracted mod. None if SRS is absent."""
    cands = []
    if game_dir:
        cands.append(ci_join(game_dir, "RuntimeSwap", "manifest.json"))
    if mods_root and mods_root.exists():
        for d in mods_root.iterdir():
            if d.is_dir():
                cands.append(ci_join(d, "RuntimeSwap", "manifest.json"))
    for m in cands:
        if m is None or not m.is_file():
            continue
        try:
            data = json.loads(m.read_text(encoding="utf-8"))
        except Exception:
            continue
        if data.get("targetVersion") and data.get("sourceVersion"):
            data["_path"] = str(m)
            data["_deployed"] = bool(game_dir) and str(m).startswith(str(game_dir))
            return data
    return None

def vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])

def print_srs_launch_help(srs: dict) -> None:
    print(tr("\nSRS виявлено: гра на диску {0} → під час запуску SKSE {1}.", srs['sourceVersion'], srs['targetVersion']))
    print(tr("Щоб SRS спрацював під Proton, а SKSE стартував з кнопки «Грати» у рідному prefix гри,"))
    print(tr("встав у Steam → Skyrim → Властивості → Параметри запуску цей рядок:"))
    print(f"  {SKSE_LAUNCH_OPTIONS}")
    print(tr("Лаунчер не підмінюй (--skse-launcher): SRS сам патчить SkyrimSELauncher.exe."))
    print(tr("Автор SRS тестував це з Proton Experimental — краще обрати саме його в «Сумісність»."))

# ===================================================================================
# Proton prefix checks: Visual C++ runtime, Steam launch options, chosen Proton version
# ===================================================================================
# Many SKSE plugins (CommonLibSSE-NG) fail with "fatal error occurred while loading plugin" on
# Wine's built-in msvcp140/vcruntime140. Wine's own DLLs carry "Wine builtin DLL" (or "Wine
# placeholder DLL") at offset 0x40 where Microsoft's have the usual DOS stub. winetricks/protontricks
# vcrun2022 copies Microsoft's DLLs and sets HKCU\Software\Wine\DllOverrides "*msvcp140"="native,builtin".
VCRUN_DLLS = ("msvcp140", "vcruntime140", "vcruntime140_1")

def dll_origin(path: Path) -> str:
    try:
        with path.open("rb") as f:
            head = f.read(0x60)
    except OSError:
        return "missing"
    if head[:2] != b"MZ":
        return "broken"
    if head[0x40:0x50] == b"Wine builtin DLL" or head[0x40:0x54] == b"Wine placeholder DLL":
        return "wine"
    return "native"

def wine_dll_overrides(prefix: Path) -> dict[str, str]:
    reg = prefix / "user.reg"
    out: dict[str, str] = {}
    try:
        text = reg.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    m = re.search(r"^\[Software\\\\Wine\\\\DllOverrides\][^\n]*\n(.*?)(?=^\[|\Z)", text, re.M | re.S | re.I)
    if m:
        for k, v in re.findall(r'^"\*?([^"]+)"="([^"]*)"', m.group(1), re.M):
            out[k.lower()] = v.lower()
    return out

def vdf_block(text: str, key: str) -> list[str]:
    """Bodies of every '"key" { ... }' block in a Valve KeyValues text (brace-aware, quote-aware)."""
    out, i = [], 0
    pat = re.compile(r'"' + re.escape(key) + r'"\s*\{')
    while True:
        m = pat.search(text, i)
        if not m:
            return out
        depth, j, inq = 1, m.end(), False
        while j < len(text) and depth:
            c = text[j]
            if inq:
                if c == "\\":
                    j += 1
                elif c == '"':
                    inq = False
            elif c == '"':
                inq = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            j += 1
        out.append(text[m.end():j - 1])
        i = j

def vdf_value(block: str, key: str) -> str | None:
    """Top-level value of key inside a block (nested blocks are skipped)."""
    depth, i = 0, 0
    pat = re.compile(r'"((?:[^"\\]|\\.)*)"')
    while i < len(block):
        c = block[i]
        if c == "{":
            depth += 1; i += 1; continue
        if c == "}":
            depth -= 1; i += 1; continue
        if c == '"':
            m = pat.match(block, i)
            if not m:
                return None
            k = m.group(1)
            j = m.end()
            m2 = re.compile(r'\s*"((?:[^"\\]|\\.)*)"').match(block, j)
            if depth == 0 and k.lower() == key.lower() and m2:
                return m2.group(1).replace('\\"', '"').replace("\\\\", "\\")
            i = m2.end() if m2 else j
            continue
        i += 1
    return None

def steam_root_of(game_dir: Path) -> list[Path]:
    roots = [Path.home() / ".local/share/Steam", Path.home() / ".steam/steam"]
    return [r for r in roots if (r / "userdata").exists() or (r / "config").exists()]

def check_proton_setup(game_dir: Path, srs: dict | None) -> list[str]:
    """Human-readable findings; lines starting with 'УВАГА' need action."""
    lines = []
    prefix = game_dir.parent.parent / "compatdata" / SKYRIM_APPID / "pfx"
    if not prefix.exists():
        return [tr("Proton-prefix гри ще не створено — запусти гру один раз через Steam")]
    sys32 = prefix / "drive_c" / "windows" / "system32"
    origins = {d: dll_origin(ci_join(sys32, d + ".dll") or sys32 / (d + ".dll")) for d in VCRUN_DLLS}
    ov = wine_dll_overrides(prefix)
    not_native = [d for d, o in origins.items() if o != "native"]
    no_override = [d for d in VCRUN_DLLS if not ov.get(d, "").startswith("n")]
    if not not_native and not no_override:
        lines.append(tr("Visual C++ runtime у prefix: справжній (Microsoft) і ввімкнений — OK"))
    else:
        if not_native:
            lines.append(tr("УВАГА: у prefix гри немає справжнього Visual C++ runtime: ") + ", ".join(
                f"{d}.dll ({tr('відсутній') if origins[d] == 'missing' else tr('заглушка Wine')})" for d in not_native))
        elif no_override:
            lines.append(tr("УВАГА: DLL Visual C++ від Microsoft є, але Wine їх не використовує (немає DllOverrides для ")
                         + ", ".join(no_override) + ")")
        lines.append(tr("  Багато SKSE-плагінів тоді падають («fatal error occurred while loading plugin»). Виправлення:"))
        lines.append(f"    flatpak run com.github.Matoking.protontricks {SKYRIM_APPID} vcrun2022")
    # Steam settings (read-only): launch options and chosen compatibility tool
    for root in steam_root_of(game_dir):
        for lc in sorted((root / "userdata").glob("*/config/localconfig.vdf")):
            try:
                text = lc.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            opts = None
            for b in vdf_block(text, SKYRIM_APPID):
                v = vdf_value(b, "LaunchOptions")
                if v is not None:
                    opts = v
            acct = lc.parent.parent.name
            if srs:
                if not opts:
                    lines.append(tr("УВАГА: у Steam (акаунт {0}) немає параметрів запуску для SRS/SKSE. Встав у Властивості → Параметри запуску:", acct))
                    lines.append(f"    {SKSE_LAUNCH_OPTIONS}")
                else:
                    if "version=n" not in opts.replace(" ", ""):
                        lines.append(tr('УВАГА: параметри запуску (акаунт {0}) без WINEDLLOVERRIDES="version=n,b" — SRS може не спрацювати', acct))
                    if "skse64_loader.exe" not in opts.lower():
                        lines.append(tr("УВАГА: параметри запуску (акаунт {0}) не запускають skse64_loader.exe — гра стартує без SKSE", acct))
                    if "version=n" in opts.replace(" ", "") and "skse64_loader.exe" in opts.lower():
                        lines.append(tr("Параметри запуску Steam (акаунт {0}): SRS і SKSE налаштовані — OK", acct))
            elif opts:
                lines.append(tr("Параметри запуску Steam (акаунт {0}): {1}", acct, opts))
        cfg = root / "config" / "config.vdf"
        if cfg.exists():
            text = cfg.read_text(encoding="utf-8", errors="replace")
            tool = None
            for blk in vdf_block(text, "CompatToolMapping"):
                for b in vdf_block(blk, SKYRIM_APPID):
                    tool = vdf_value(b, "name") or tool
            if tool:
                lines.append(tr("Proton для гри: {0}", tool))
                if srs and "experimental" not in tool.lower():
                    lines.append(tr("  (автор SRS тестував його з Proton Experimental — якщо SRS не спрацює, обери його)"))
            else:
                lines.append(tr("Proton для гри: типовий для Steam (не задано окремо)"))
        break
    return lines

# ===================================================================================
# SKSE through Steam's "Play" button: SkyrimSELauncher.exe -> skse64_loader.exe
# ===================================================================================
# Steam starts SkyrimSELauncher.exe inside the game's own Proton prefix (right language, saves, settings).
# A separate non-Steam shortcut to skse64_loader.exe gets a NEW prefix with default settings instead.
LAUNCHER_JOURNAL = "skse_launcher.json"

def install_skse_launcher(game_dir: Path, output_root: Path) -> None:
    print(tr("\n=== SKSE через кнопку «Грати» ==="))
    if game_running():
        print(tr("Гра запущена — закрий її спершу."))
        return
    srs = find_srs(game_dir, output_root.parent / "Mods")
    if srs:
        print(tr("Не підмінюю лаунчер: у збірці є Skyrim Runtime Swapper, а він сам патчить SkyrimSELauncher.exe."))
        print_srs_launch_help(srs)
        return
    jpath = output_root / LAUNCHER_JOURNAL
    launcher = ci_join(game_dir, "SkyrimSELauncher.exe")
    loader = ci_join(game_dir, "skse64_loader.exe")
    if loader is None:
        print(tr("skse64_loader.exe немає в папці гри. Спершу --deploy (мод SKSE) або встанови SKSE вручну."))
        return
    gv = pe_file_version(ci_join(game_dir, "SkyrimSE.exe")) if ci_join(game_dir, "SkyrimSE.exe") else None
    if gv:
        want = "skse64_{}_{}_{}.dll".format(*gv[:3])  # (with SRS present this function returned above)
        if ci_join(game_dir, want) is None:
            print(tr("Немає {0} — цей SKSE не для гри {1}. Лаунчер не змінено.", want, '.'.join(map(str, gv))))
            return
    if jpath.exists():
        j = json.loads(jpath.read_text(encoding="utf-8"))
        if launcher and launcher.stat().st_size == loader.stat().st_size and file_digest(launcher) == file_digest(loader):
            print(tr("Уже встановлено: «Грати» в Steam запускає SKSE."))
            return
        # Steam probably restored the original launcher (update / file verification): back it up again.
        jpath.unlink()
    rec = {"launcher": None, "backup": None}
    if launcher is not None:
        bk = output_root / "backup" / "launcher" / launcher.name
        bk.parent.mkdir(parents=True, exist_ok=True)
        if bk.exists():
            bk.unlink()
        rec.update(launcher=launcher.name, backup=str(bk.relative_to(output_root)))
        jpath.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")  # journal before move
        shutil.move(str(launcher), bk)
        target = launcher
    else:
        target = game_dir / "SkyrimSELauncher.exe"
        rec["launcher"] = target.name
    # A copy, not a hard link: --undeploy may remove skse64_loader.exe, the launcher must keep working.
    shutil.copy2(loader, target)
    st = target.stat()
    rec.update(size=st.st_size, sha1=file_digest(target), at=datetime.now().astimezone().isoformat(timespec="seconds"))
    jpath.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    print(tr("Готово: кнопка «Грати» в Steam тепер запускає SKSE у рідному prefix гри."))
    print(tr("Оригінальний лаунчер збережено; повернути: --skse-launcher-restore"))
    print(tr("Окремий ярлик на skse64_loader.exe у Steam більше не потрібен (його prefix можна видалити разом з ярликом)."))

def restore_skse_launcher(game_dir: Path, output_root: Path) -> None:
    print(tr("\n=== Повернення оригінального лаунчера ==="))
    if game_running():
        print(tr("Гра запущена — закрий її спершу."))
        return
    jpath = output_root / LAUNCHER_JOURNAL
    if not jpath.exists():
        print(tr("Лаунчер не змінювався — нічого повертати."))
        return
    j = json.loads(jpath.read_text(encoding="utf-8"))
    current = game_dir / j["launcher"]
    bk = output_root / j["backup"] if j.get("backup") else None
    ours = current.exists() and current.stat().st_size == j.get("size") and file_digest(current) == j.get("sha1")
    if current.exists() and not ours:
        # Steam already put the original back (update / verification). Keep what's there.
        print(tr("Лаунчер уже не наш (Steam, мабуть, відновив оригінал) — залишаю як є."))
        if bk and bk.exists():
            bk.unlink()
    else:
        if ours:
            current.unlink()
        if bk and bk.exists():
            shutil.move(str(bk), current)
            print(tr("Оригінальний SkyrimSELauncher.exe повернено."))
        else:
            print(tr("Лаунчер SKSE прибрано (оригіналу не було)."))
    jpath.unlink()
    bdir = output_root / "backup"
    if bdir.exists() and not any(p.is_file() for p in bdir.rglob("*")):
        shutil.rmtree(bdir)

# ===================================================================================
# Main
# ===================================================================================
# ===================================================================================
# Game backups: a snapshot of the clean game folder (plus plugins.txt and the game's INI files in the
# Proton prefix) to return to after a broken setup or an unwanted Steam update.
#   full — real copies: safe against anything, needs as much free space as the game takes;
#   link — hard links: no extra space, survives files being deleted or replaced (new file), but NOT
#          files edited in place (the backup is the same file). Every file's SHA-1 is stored, so a
#          damaged link backup is detected before restoring instead of silently restoring garbage.
# Saves are never touched unless asked (--with-saves copies them; restore only adds missing ones).
# ===================================================================================
GAME_BACKUPS_DIR = "game_backups"
BACKUP_NAME_RE = re.compile(r"[\w][\w .()-]{0,59}")
SUSPECT_ROOT_RE = re.compile(r"^(skse64_.*|dinput8\.dll|d3d11\.dll|dxgi\.dll|d3dx9_42\.dll|d3dcompiler_46e\.dll|"
                             r"enb.*|reshade.*|version\.dll|winhttp\.dll)$", re.I)

def game_backups_root(output_root: Path) -> Path:
    return output_root / GAME_BACKUPS_DIR

def prefix_dirs(game_dir: Path) -> tuple[Path, Path]:
    """(AppData/Local/<game>, Documents/My Games/<game>) inside the game's Proton prefix."""
    user = game_dir.parent.parent / "compatdata" / SKYRIM_APPID / "pfx/drive_c/users/steamuser"
    appdata = user / "AppData/Local"
    docs = user / "Documents/My Games"
    def pick(base: Path) -> Path:
        for n in MY_GAMES_NAMES:
            d = ci_join(base, n) if base.exists() else None
            if d is not None:
                return d
        return base / MY_GAMES_NAMES[0]
    return pick(appdata), pick(docs)

def game_files(game_dir: Path, exclude: list[Path]) -> list[str]:
    """Every file of the game folder, except this engine's own folders and scripts."""
    excl = {p.resolve() for p in exclude}
    out = []
    for dp, dns, fns in os.walk(game_dir):
        d = Path(dp)
        dns[:] = [n for n in dns if (d / n).resolve() not in excl]
        for f in fns:
            p = d / f
            if p.resolve() in excl or p.is_symlink():
                continue
            out.append(p.relative_to(game_dir).as_posix())
    return sorted(out)

def sha1_and_copy(src: Path, dst: Path | None) -> str:
    h = hashlib.sha1()
    with src.open("rb") as f, (dst.open("wb") if dst else open(os.devnull, "wb")) as g:
        for block in iter(lambda: f.read(4 << 20), b""):
            h.update(block)
            g.write(block)
    if dst:
        shutil.copystat(src, dst)
    return h.hexdigest()

def suspicious_files(files: list[str]) -> list[str]:
    """Things a vanilla game does not ship: script extenders, ENB/ReShade, loose folders in Data."""
    out = []
    for rel in files:
        parts = rel.split("/")
        if len(parts) == 1 and SUSPECT_ROOT_RE.match(parts[0]):
            out.append(rel)
        elif len(parts) >= 3 and parts[0].lower() == "data" and parts[1].lower() not in ("video", "strings"):
            out.append(parts[0] + "/" + parts[1] + "/")
    return sorted(set(out))

def backup_game(game_dir: Path, output_root: Path, exclude: list[Path], name: str | None, mode: str,
                with_saves: bool) -> int:
    if game_running():
        print(tr("Гра запущена — закрий її спершу."))
        return 1
    if load_journal(output_root):
        print(tr("Зараз розгорнуто моди — це вже не чиста гра. Спершу --undeploy, потім бекап, потім знову --deploy."))
        return 1
    exe = ci_join(game_dir, "SkyrimSE.exe")
    gv = pe_file_version(exe) if exe else None
    ver = ".".join(map(str, gv)) if gv else "?"
    name = name or f"{ver} {datetime.now().strftime('%Y-%m-%d %H-%M')}"
    if not BACKUP_NAME_RE.fullmatch(name):
        print(tr("Недопустима назва бекапу: «{0}»", name))
        return 1
    root = game_backups_root(output_root)
    dest = root / name
    if dest.exists():
        print(tr("Бекап «{0}» уже існує.", name))
        return 1
    files = game_files(game_dir, exclude)
    total = sum((game_dir / f).stat().st_size for f in files)
    sus = suspicious_files(files)
    print(tr("\n=== Бекап гри «{0}» ({1}) ===", name, tr("повні копії") if mode == "full" else tr("жорсткі посилання")))
    print(tr("Гра {0}: {1} файлів, {2}", ver, len(files), human_size(total)))
    if sus:
        print(tr("УВАГА: схоже, гра вже не зовсім чиста (це потрапить у бекап): {0}", ", ".join(sus[:12]) + (" …" if len(sus) > 12 else "")))
    root.mkdir(parents=True, exist_ok=True)
    if mode == "full":
        free = shutil.disk_usage(root).free
        if free < total + (1 << 30):
            print(tr("Недостатньо місця: потрібно {0}, вільно {1}. Спробуй --backup-mode link (без зайвого місця).", human_size(total + (1 << 30)), human_size(free)))
            return 1
    tmp = root / f".{name}.partial"
    shutil.rmtree(tmp, ignore_errors=True)
    manifest = {"name": name, "created": datetime.now().astimezone().isoformat(timespec="seconds"),
                "game_version": ver, "mode": mode, "game_dir": str(game_dir), "files": {}, "prefix": [],
                "saves": with_saves, "suspicious": sus, "size": total}
    done_bytes, step = 0, max(total // 10, 1)
    nxt = step
    copied = 0
    for rel in files:
        src = game_dir / rel
        dst = tmp / "files" / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        st = src.stat()
        if mode == "link":
            try:
                os.link(src, dst)
                digest = sha1_and_copy(src, None)
            except OSError:
                digest = sha1_and_copy(src, dst)
                copied += 1
        else:
            digest = sha1_and_copy(src, dst)
        manifest["files"][rel] = [st.st_size, st.st_mtime_ns, digest]
        done_bytes += st.st_size
        if done_bytes >= nxt:
            print(tr("  {0} / {1}", human_size(done_bytes), human_size(total)), flush=True)
            while nxt <= done_bytes:
                nxt += step
    appdata, docs = prefix_dirs(game_dir)
    extra = []
    for base, sub in ((appdata, "AppData"), (docs, "Documents")):
        if base.is_dir():
            for p in sorted(base.iterdir()):
                if p.is_file() and (p.suffix.lower() == ".ini" or p.name.lower() in ("plugins.txt", "loadorder.txt")):
                    extra.append((p, f"{sub}/{p.name}"))
            if with_saves and sub == "Documents" and ci_join(base, "Saves"):
                for p in sorted(ci_join(base, "Saves").rglob("*")):
                    if p.is_file():
                        extra.append((p, f"Saves/{p.relative_to(ci_join(base, 'Saves')).as_posix()}"))
    for p, rel in extra:
        d = tmp / "prefix" / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, d)
        manifest["prefix"].append(rel)
    (tmp / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=0), encoding="utf-8")
    tmp.rename(dest)  # complete only now: an interrupted backup stays ".partial" and is never offered
    print(tr("Бекап готовий: {0} (налаштувань із prefix: {1}{2})", dest, len(manifest["prefix"]),
             tr(", разом із сейвами") if with_saves else ""))
    if mode == "link":
        print(tr("  Режим link: місця не займає, але якщо Steam змінить файл на місці, змінюється й бекап — перевірка: --backup-verify"))
        if copied:
            print(tr("  {0} файл(ів) на іншому диску скопійовано повністю", copied))
    return 0

def load_backup(output_root: Path, name: str) -> dict | None:
    try:
        m = json.loads((game_backups_root(output_root) / name / "manifest.json").read_text(encoding="utf-8"))
        return m if m.get("files") else None
    except Exception:
        return None

def list_backups(output_root: Path) -> list[dict]:
    root = game_backups_root(output_root)
    out = []
    for d in sorted(root.iterdir()) if root.exists() else []:
        if d.is_dir() and not d.name.startswith("."):
            m = load_backup(output_root, d.name)
            if m:
                out.append({k: m.get(k) for k in ("name", "created", "game_version", "mode", "size", "saves", "suspicious")}
                           | {"files": len(m["files"])})
    return out

def print_backups(output_root: Path) -> None:
    bs = list_backups(output_root)
    print(tr("\nБекапи гри:") if bs else tr("\nБекапів гри ще немає (--backup-game)."))
    for b in bs:
        print(f"  {b['name']}  —  {b['game_version']}, {b['files']} " + tr("файлів") + f", {human_size(b['size'] or 0)}, {b['mode']}, {b['created']}")

def verify_backup(output_root: Path, name: str, quiet: bool = False) -> list[str]:
    """Files of the backup that are missing or no longer match their recorded SHA-1."""
    m = load_backup(output_root, name)
    if not m:
        return ["manifest.json"]
    base = game_backups_root(output_root) / name / "files"
    bad = []
    for n, (rel, (size, _mt, digest)) in enumerate(sorted(m["files"].items()), 1):
        p = base / rel
        if not p.is_file() or p.stat().st_size != size or sha1_and_copy(p, None) != digest:
            bad.append(rel)
        if not quiet and n % 2000 == 0:
            print(tr("  перевірено {0}/{1}...", n, len(m["files"])), flush=True)
    return bad

def restore_game(game_dir: Path, output_root: Path, exclude: list[Path], name: str) -> int:
    m = load_backup(output_root, name)
    if not m:
        print(tr("Бекапу «{0}» немає. Є: {1}", name, ", ".join(b["name"] for b in list_backups(output_root)) or "—"))
        return 1
    if game_running():
        print(tr("Гра запущена — закрий її спершу."))
        return 1
    print(tr("\n=== Відновлення гри з бекапу «{0}» ({1}) ===", name, m["game_version"]))
    base = game_backups_root(output_root) / name / "files"
    aside = output_root / "restore_aside" / datetime.now().strftime("%Y%m%d-%H%M%S")
    def put_aside(p: Path, rel: str) -> None:
        d = aside / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), d)
    def changed() -> list[str]:
        out = []
        for rel, (size, mtime, _digest) in m["files"].items():
            try:
                st = (game_dir / rel).stat()
                if st.st_size == size and st.st_mtime_ns == mtime:
                    continue  # unchanged since the backup
            except FileNotFoundError:
                pass
            out.append(rel)
        return out
    # 1) Check the backup itself before touching anything (deployed mods count as changes here too).
    broken = [rel for rel in changed() if not (base / rel).is_file() or sha1_and_copy(base / rel, None) != m["files"][rel][2]]
    if broken:
        print(tr("Бекап пошкоджений, відновлення скасовано (гру не змінено). Файли не збігаються з записаними: {0}",
                 ", ".join(broken[:8]) + (" …" if len(broken) > 8 else "")))
        if m["mode"] == "link":
            print(tr("  Це бекап у режимі link: файл змінили на місці (найчастіше оновлення Steam). Для захисту від оновлень потрібен --backup-mode full."))
        return 1
    if load_journal(output_root):
        print(tr("Спершу прибираю розгорнуті моди..."))
        undeploy(game_dir, output_root)
    todo = changed()
    # 2) Replace changed/missing files; move files that are not in the backup aside (never deleted).
    restored = 0
    for rel in todo:
        t = game_dir / rel
        if t.exists() or t.is_symlink():
            put_aside(t, rel)
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(base / rel, t)  # a copy: the backup must not change when the game file does
        restored += 1
    known = {r.lower() for r in m["files"]}
    extra = [r for r in game_files(game_dir, exclude) if r.lower() not in known]
    for rel in extra:
        put_aside(game_dir / rel, rel)
    for dp, dns, fns in os.walk(game_dir, topdown=False):  # empty folders left by moved-aside files
        d = Path(dp)
        if d != game_dir and not any(d.iterdir()) and d.resolve() not in {p.resolve() for p in exclude}:
            try:
                d.rmdir()
            except OSError:
                pass
    # 3) Prefix settings (INI, plugins.txt); saves only added, never overwritten.
    appdata, docs = prefix_dirs(game_dir)
    pbase = game_backups_root(output_root) / name / "prefix"
    prefixed = 0
    for rel in m.get("prefix", []):
        src = pbase / rel
        kind, _, rest = rel.partition("/")
        target = (appdata if kind == "AppData" else docs / "Saves" if kind == "Saves" else docs) / rest
        if kind == "Saves":
            if target.exists():
                continue
        elif target.exists():
            if target.read_bytes() == src.read_bytes():
                continue
            put_aside(target, f"_prefix/{rel}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        prefixed += 1
    print(tr("Гру відновлено: файлів повернено {0}, зайвих відкладено {1}, налаштувань prefix {2}.", restored, len(extra), prefixed))
    if restored or extra or prefixed:
        print(tr("  Замінене й зайве лежить у {0} — нічого не видалено.", aside))
    print(tr("  Щоб Steam не оновив гру знову: Властивості → Оновлення — вибери оновлення лише під час запуску, а запускай через SKSE."))
    return 0

def delete_backup(output_root: Path, name: str) -> int:
    d = game_backups_root(output_root) / name
    if not load_backup(output_root, name):
        print(tr("Бекапу «{0}» немає.", name))
        return 1
    shutil.rmtree(d)
    print(tr("Бекап «{0}» видалено.", name))
    return 0

# --- Baseline: list + SHA-1 of every game file, made on the first run ---------------------------------
# Even without a full backup it tells, in a broken game, which files are not the game's own (moved aside
# by --game-repair) and which original files were changed or are missing (restored from a backup that has
# them, otherwise Steam's "Verify integrity of game files" brings them back).
GAME_BASELINE = "game_baseline.json"

def deployed_paths(output_root: Path) -> dict[str, dict]:
    j = load_journal(output_root)
    return {e["path"].lower(): e for e in (j or {}).get("planned", [])} if j else {}

def make_baseline(game_dir: Path, output_root: Path, exclude: list[Path]) -> dict:
    """Hash the game as it is without our mods: files we deployed are skipped and the originals they
    replaced are taken from the deployment backup, so a baseline can be made at any time."""
    planned = deployed_paths(output_root)
    bk_root = output_root / "backup" / "files"
    files = game_files(game_dir, exclude)
    sizes = {rel: (game_dir / rel).stat().st_size for rel in files}
    total = sum(sizes.values())
    print(tr("Список файлів гри з SHA-1: {0} файлів, {1} — це займе трохи часу...", len(files), human_size(total)), flush=True)
    out: dict[str, list] = {}
    done, step = 0, max(total // 5, 1)
    nxt = step
    for rel in files:
        src = game_dir / rel
        if rel.lower() in planned:
            src = bk_root / rel  # the original our deployment replaced (absent: the file is a mod's)
            if not src.is_file():
                continue
        st = src.stat()
        out[rel] = [st.st_size, st.st_mtime_ns, sha1_and_copy(src, None)]
        done += sizes[rel]
        if done >= nxt:
            print(tr("  {0} / {1}", human_size(done), human_size(total)), flush=True)
            while nxt <= done:
                nxt += step
    exe = ci_join(game_dir, "SkyrimSE.exe")
    gv = pe_file_version(exe) if exe else None
    base = {"created": datetime.now().astimezone().isoformat(timespec="seconds"),
            "game_version": ".".join(map(str, gv)) if gv else "?", "files": out,
            "suspicious": suspicious_files(list(out))}
    (output_root / GAME_BASELINE).write_text(json.dumps(base, ensure_ascii=False, indent=0), encoding="utf-8")
    print(tr("Список файлів гри збережено ({0}, гра {1}).", GAME_BASELINE, base["game_version"]))
    if base["suspicious"]:
        print(tr("УВАГА: у грі вже є схоже на моди (у список потрапило як «рідне»): {0}", ", ".join(base["suspicious"][:10])))
    return base

def load_baseline(output_root: Path) -> dict | None:
    try:
        return json.loads((output_root / GAME_BASELINE).read_text(encoding="utf-8"))
    except Exception:
        return None

def protection_notice() -> None:
    print(tr("Копіювання файлів гри для захисту вимкнене, доки ти сам не вирішиш (місце на диску):"))
    print(tr("  --incremental-backup on — копії лише тих оригіналів, які перекривають моди; --backup-game — повний бекап чистої гри."))
    print(tr("  У GUI — сторінка «Резервні копії». Примітка зникне після вибору."))

def first_run_protection(game_dir: Path, output_root: Path, exclude: list[Path]) -> None:
    """Asked once in a terminal: the two options that copy files (and so take disk space)."""
    print(tr("\n=== Захист гри (одноразове налаштування) ==="))
    clean = not load_journal(output_root)
    size = sum((game_dir / r).stat().st_size for r in game_files(game_dir, exclude))
    print(tr("1) Інкрементальний бекап — копія кожного оригінального файлу гри, який перекриває мод (місце — лише ці файли)."))
    print(tr("2) Повний бекап чистої гри — повні копії ({0}) або посилання (без місця, але не захищає від змін на місці).", human_size(size)))
    yes = ("", "y", "yes", "т", "так")
    a1 = ask(tr("Увімкнути інкрементальний бекап (1)? [Y/n]: ")) in yes
    a2 = (ask(tr("Повний бекап (2)? [f] повні копії / [l] посилання / [n] ні [n]: ")) or "n")[:1] if clean else "n"
    update_settings(output_root, protection_asked=True, incremental_backup=a1)
    n_all, b_all = store_stats(output_root)
    print(tr("Інкрементальний бекап при розгортанні: {0}. У сховищі {1} файл(ів), {2}.",
             tr("увімкнено") if a1 else tr("вимкнено"), n_all, human_size(b_all)))
    if a2 in ("f", "l"):
        backup_game(game_dir, output_root, exclude, None, "full" if a2 == "f" else "link", False)
    elif not clean:
        print(tr("Повний бекап можна зробити, коли моди не розгорнуто: --undeploy, --backup-game, --deploy."))
    print(tr("Змінити будь-коли: --incremental-backup on|off, --backup-game (або в GUI, «Резервні копії»)."))

def game_check(game_dir: Path, output_root: Path, exclude: list[Path]) -> dict | None:
    """Compare the game with the baseline. Files deployed by us are reported separately, not as damage."""
    base = load_baseline(output_root)
    if not base:
        print(tr("Немає списку файлів гри — створи: --game-baseline"))
        return None
    planned = deployed_paths(output_root)
    known = {r.lower() for r in base["files"]}
    res = {"changed": [], "missing": [], "extra": [], "deployed_over": []}
    for rel, (size, mtime, digest) in base["files"].items():
        p = game_dir / rel
        if rel.lower() in planned:
            res["deployed_over"].append(rel)
            continue
        try:
            st = p.stat()
        except FileNotFoundError:
            res["missing"].append(rel)
            continue
        if st.st_size == size and st.st_mtime_ns == mtime:
            continue
        if st.st_size != size or sha1_and_copy(p, None) != digest:
            res["changed"].append(rel)
    for rel in game_files(game_dir, exclude):
        if rel.lower() not in known and rel.lower() not in planned:
            res["extra"].append(rel)
    exe = ci_join(game_dir, "SkyrimSE.exe")
    gv = pe_file_version(exe) if exe else None
    res["game_version"] = ".".join(map(str, gv)) if gv else "?"
    res["baseline_version"] = base.get("game_version")
    return res

def print_game_check(res: dict) -> None:
    print(tr("\n=== Перевірка гри (порівняно зі списком від гри {0}) ===", res["baseline_version"]))
    if res["game_version"] != res["baseline_version"]:
        print(tr("УВАГА: версія гри змінилась: {0} → {1} (оновлення Steam?). Якщо так і треба — онови список: --game-baseline", res["baseline_version"], res["game_version"]))
    if not (res["changed"] or res["missing"] or res["extra"]):
        print(tr("Гра відповідає списку — чиста."))
    for key, title in (("changed", tr("Змінені файли гри")), ("missing", tr("Відсутні файли гри")), ("extra", tr("Зайві файли (не з гри і не розгорнуті скриптом)"))):
        if res[key]:
            print(f"{title} ({len(res[key])}):")
            for r in res[key][:15]:
                print(f"  {r}")
            if len(res[key]) > 15:
                print(tr("  ...і ще {0}", len(res[key]) - 15))
    if res["deployed_over"]:
        print(tr("Перекрито розгорнутими модами (повернеться через --undeploy): {0}", len(res["deployed_over"])))
    if res["changed"] or res["missing"] or res["extra"]:
        print(tr("Виправити: --game-repair"))

def game_repair(game_dir: Path, output_root: Path, exclude: list[Path]) -> int:
    if game_running():
        print(tr("Гра запущена — закрий її спершу."))
        return 1
    res = game_check(game_dir, output_root, exclude)
    if res is None:
        return 1
    print_game_check(res)
    if not (res["changed"] or res["missing"] or res["extra"]):
        return 0
    base = load_baseline(output_root)
    aside = output_root / "repair_aside" / datetime.now().strftime("%Y%m%d-%H%M%S")
    for rel in res["extra"]:
        d = aside / rel
        d.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(game_dir / rel), d)
    for dp, dns, fns in os.walk(game_dir, topdown=False):
        d = Path(dp)
        if d != game_dir and d.resolve() not in {p.resolve() for p in exclude} and not any(d.iterdir()):
            try:
                d.rmdir()
            except OSError:
                pass
    # Changed/missing originals: take them from any game backup that has the same file (same SHA-1).
    sources = []
    for b in list_backups(output_root):
        m = load_backup(output_root, b["name"])
        if m:
            sources.append((game_backups_root(output_root) / b["name"] / "files", m["files"]))
    fixed, left = 0, []
    store = store_index(output_root)["objects"]
    for rel in res["changed"] + res["missing"]:
        want = base["files"][rel][2]
        obj = store_object(output_root, want)
        src = obj if want in store and obj.is_file() and sha1_and_copy(obj, None) == want else None
        src = src or next((root / rel for root, files in sources if files.get(rel, [0, 0, ""])[2] == want
                           and (root / rel).is_file() and sha1_and_copy(root / rel, None) == want), None)
        if src is None:
            left.append(rel)
            continue
        t = game_dir / rel
        if t.exists():
            d = aside / rel
            d.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(t), d)
        t.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, t)
        fixed += 1
    print(tr("Ремонт: зайвих відкладено {0}, файлів гри відновлено з бекапу {1}.", len(res["extra"]), fixed))
    if res["extra"] or fixed:
        print(tr("  Прибране лежить у {0} — нічого не видалено.", aside))
    if left:
        print(tr("Ще {0} файл(ів) гри змінено або бракує, а в бекапах їх немає: Steam → Skyrim → Властивості → Встановлені файли → «Перевірити цілісність файлів гри». Напр.: {1}",
                 len(left), ", ".join(left[:5])))
    return 0

# --- Incremental backup at deployment: a copy of every original game file a deployment overwrites ------
# Kept permanently and stored once per content (SHA-1), so it costs only the size of the files mods
# actually replace. --game-repair and --restore-game take originals from here too.
GAME_STORE = "game_store"

def store_index(output_root: Path) -> dict:
    try:
        return json.loads((output_root / GAME_STORE / "index.json").read_text(encoding="utf-8"))
    except Exception:
        return {"objects": {}, "paths": {}}

def store_object(output_root: Path, digest: str) -> Path:
    return output_root / GAME_STORE / "objects" / digest[:2] / digest

def store_stats(output_root: Path) -> tuple[int, int]:
    idx = store_index(output_root)
    return len(idx["objects"]), sum(o.get("size", 0) for o in idx["objects"].values())

def incremental_enabled(output_root: Path) -> bool:
    return read_settings(output_root).get("incremental_backup") is True  # opt-in

def incremental_backup(game_dir: Path, output_root: Path, rels: list[str], dry: bool) -> tuple[int, int]:
    """Save the current versions of game files about to be overwritten. Returns (new files, new bytes)."""
    idx = store_index(output_root)
    base = load_baseline(output_root) or {"files": {}}
    exe = ci_join(game_dir, "SkyrimSE.exe")
    gv = pe_file_version(exe) if exe else None
    ver = ".".join(map(str, gv)) if gv else "?"
    new_n = new_b = 0
    for rel in rels:
        p = game_dir / rel
        try:
            st = p.stat()
        except FileNotFoundError:
            continue
        known = idx["paths"].get(rel)
        if known and known[0] == st.st_size and known[1] == st.st_mtime_ns and known[2] in idx["objects"]:
            continue  # already stored (same file as last time)
        digest = sha1_and_copy(p, None)
        if digest not in idx["objects"] or not store_object(output_root, digest).is_file():
            new_n += 1
            new_b += st.st_size
            if not dry:
                obj = store_object(output_root, digest)
                obj.parent.mkdir(parents=True, exist_ok=True)
                tmp = obj.with_name(obj.name + ".tmp")
                if sha1_and_copy(p, tmp) != digest:
                    tmp.unlink()
                    raise OSError(tr("файл {0} змінився під час копіювання", rel))
                tmp.replace(obj)
                idx["objects"][digest] = {"size": st.st_size, "game_version": ver,
                                          "baseline": base["files"].get(rel, [0, 0, ""])[2] == digest,
                                          "saved": datetime.now().astimezone().isoformat(timespec="seconds")}
        if not dry:
            idx["objects"].setdefault(digest, {"size": st.st_size, "game_version": ver})
            idx["objects"][digest].setdefault("paths", [])
            if rel not in idx["objects"][digest]["paths"]:
                idx["objects"][digest]["paths"].append(rel)
            idx["paths"][rel] = [st.st_size, st.st_mtime_ns, digest]
    if not dry:
        (output_root / GAME_STORE).mkdir(parents=True, exist_ok=True)
        t = output_root / GAME_STORE / "index.json.tmp"
        t.write_text(json.dumps(idx, ensure_ascii=False, indent=0), encoding="utf-8")
        t.replace(output_root / GAME_STORE / "index.json")
    return new_n, new_b

def baseline_drift(game_dir: Path, output_root: Path) -> list[str]:
    """Game files whose size or time differ from the clean list (quick check, no hashing)."""
    base = load_baseline(output_root)
    out = []
    for rel, (size, mtime, _d) in (base or {}).get("files", {}).items():
        try:
            st = (game_dir / rel).stat()
            if st.st_size != size or st.st_mtime_ns != mtime:
                out.append(rel)
        except FileNotFoundError:
            out.append(rel)
    return out

# ===================================================================================
# Nexus Mods: the user's own API key (Nexus account → Site preferences → API Keys → Personal API key),
# downloads of mod files into mods-archives/. Premium accounts can download directly (also the
# missing archives of a collection); free accounts download through "Mod Manager Download" buttons on
# the site, which open nxm:// links handled by --nxm (register with --nxm-handler).
# The key is kept only on this computer, in ~/.config/skyrim-mod-engine/nexus.json (mode 600).
# ===================================================================================
NEXUS_API = os.environ.get("SME_NEXUS_API", "https://api.nexusmods.com")
NEXUS_GAME = "skyrimspecialedition"
APP_NAME = "Skyrim Mod Engine"
APP_VERSION = "0.10"
NXM_DESKTOP = "skyrim-mod-engine-nxm.desktop"

class NexusError(RuntimeError):
    pass

def nexus_config_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "skyrim-mod-engine" / "nexus.json"

def nexus_account() -> dict | None:
    try:
        data = json.loads(nexus_config_path().read_text(encoding="utf-8"))
        return data if data.get("api_key") else None
    except Exception:
        return None

def nexus_save_account(data: dict) -> None:
    p = nexus_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.chmod(p, 0o600)

def _nexus_headers(key: str | None) -> dict:
    h = {"Application-Name": APP_NAME, "Application-Version": APP_VERSION, "Accept": "application/json",
         "User-Agent": f"{APP_NAME}/{APP_VERSION} (Linux; Python {sys.version.split()[0]})"}
    if key:
        h["apikey"] = key
    return h

def nexus_get(path: str, key: str, params: dict | None = None, body: dict | None = None):
    """GET (or POST with a JSON body) to the Nexus API; errors become NexusError with a readable text."""
    import urllib.request
    import urllib.error
    import urllib.parse
    url = (path if path.startswith("http") else NEXUS_API + path) + (("?" + urllib.parse.urlencode(params)) if params else "")
    headers = _nexus_headers(key)
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as ex:
        body = ex.read()[:500].decode("utf-8", "replace")
        try:
            msg = json.loads(body).get("message") or body
        except Exception:
            msg = body
        if ex.code == 401:
            raise NexusError(tr("Nexus не прийняв API-ключ (недійсний або відкликаний). Увійди знову: --nexus-login"))
        if ex.code == 403:
            raise NexusError(tr("Nexus відмовив у доступі: {0}", msg.strip() or ex.code))
        if ex.code == 404:
            raise NexusError(tr("Nexus: не знайдено ({0})", path))
        if ex.code == 429:
            raise NexusError(tr("Nexus: вичерпано ліміт запитів API, спробуй пізніше (скидання: {0})",
                                ex.headers.get("x-rl-hourly-reset") or ex.headers.get("x-rl-daily-reset") or "?"))
        raise NexusError(tr("Nexus: помилка {0}: {1}", ex.code, msg.strip()[:200]))
    except urllib.error.URLError as ex:
        raise NexusError(tr("Немає з'єднання з Nexus: {0}", ex.reason))

def nexus_login(key: str | None) -> dict | None:
    if not key:
        if not sys.stdin.isatty():
            print(tr("Вкажи ключ: --nexus-login КЛЮЧ (Nexus → Site preferences → API Keys → Personal API key)"))
            return None
        import getpass
        print(tr("Персональний API-ключ: nexusmods.com → аватар → Site preferences → API Keys → Personal API key"))
        key = getpass.getpass(tr("Встав ключ (не відображається): ")).strip()
        if not key:
            return None
    key = key.strip()
    try:
        u = nexus_get("/v1/users/validate.json", key)
    except NexusError as ex:
        print(tr("ПОМИЛКА входу: {0}", ex))
        return None
    acc = {"api_key": key, "name": u.get("name"), "user_id": u.get("user_id"),
           "is_premium": bool(u.get("is_premium", u.get("is_premium?"))),
           "checked_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    nexus_save_account(acc)
    print(tr("Вхід у Nexus: {0} ({1}). Ключ збережено в {2}", acc["name"],
             tr("Premium") if acc["is_premium"] else tr("безкоштовний акаунт"), nexus_config_path()))
    if not acc["is_premium"]:
        print(tr("Без Premium Nexus дозволяє завантаження лише кнопкою «Mod Manager Download» на сайті (посилання nxm://). Зареєструй обробник: --nxm-handler"))
    return acc

def nexus_logout() -> None:
    p = nexus_config_path()
    if p.exists():
        p.unlink()
        print(tr("Ключ Nexus видалено з цього комп'ютера."))
    else:
        print(tr("Вхід у Nexus не виконано."))

def nexus_status() -> None:
    acc = nexus_account()
    if not acc:
        print(tr("Nexus: вхід не виконано (--nexus-login)."))
        return
    print(tr("Nexus: {0} ({1})", acc.get("name"), tr("Premium") if acc.get("is_premium") else tr("безкоштовний акаунт")))

def parse_nxm(url: str) -> dict:
    """nxm://skyrimspecialedition/mods/<mod>/files/<file>?key=..&expires=..&user_id=.."""
    from urllib.parse import urlparse, parse_qs
    u = urlparse(url.strip())
    m = re.fullmatch(r"/mods/(\d+)/files/(\d+)/?", u.path or "")
    if u.scheme.lower() != "nxm" or not m:
        raise NexusError(tr("Не схоже на посилання nxm:// на файл мода: {0}", url[:120]))
    q = parse_qs(u.query)
    return {"game": u.netloc.lower(), "mod": int(m.group(1)), "file": int(m.group(2)),
            "key": (q.get("key") or [None])[0], "expires": (q.get("expires") or [None])[0]}

def safe_archive_name(name: str) -> str:
    name = Path(str(name).replace("\\", "/")).name
    name = re.sub(r'[\x00-\x1f/]', "_", name).strip(" .")
    if not name:
        raise NexusError(tr("Nexus повернув порожню назву файлу"))
    return name

def nexus_pick_file(mod: int, key: str, game: str = NEXUS_GAME) -> int:
    """The main file of a mod: primary MAIN file, else the newest MAIN file."""
    data = nexus_get(f"/v1/games/{game}/mods/{mod}/files.json", key, {"category": "main"})
    files = [f for f in data.get("files", []) if str(f.get("category_name", "")).upper() == "MAIN"] or data.get("files", [])
    if not files:
        raise NexusError(tr("У мода {0} немає основних файлів для завантаження", mod))
    files.sort(key=lambda f: (bool(f.get("is_primary")), f.get("uploaded_timestamp") or 0), reverse=True)
    if len(files) > 1:
        print(tr("У мода {0} кілька основних файлів, беру «{1}». Інші: {2}", mod, files[0].get("name"),
                 ", ".join(f"{f.get('file_id')} ({f.get('name')})" for f in files[1:6])))
    return int(files[0]["file_id"])

def http_download(url: str, part: Path, size: int = 0) -> None:
    """Stream url into part, resuming a partial file; retries a dropped connection twice."""
    import urllib.request
    import urllib.error
    exact = size > 0
    for attempt in range(3):  # a dropped connection is resumed from where it stopped
        have = part.stat().st_size if part.exists() else 0
        if exact and have == size:
            break
        headers = {"User-Agent": _nexus_headers(None)["User-Agent"]}
        if have:
            headers["Range"] = f"bytes={have}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                if have and r.status != 206:
                    have = 0  # the server ignored Range: start over
                done, step, t0 = have, max(size // 10, 32 << 20) if size else 64 << 20, time.time()
                nxt = done + step
                with part.open("ab" if have else "wb") as f:
                    while True:
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        done += len(chunk)
                        if done >= nxt:
                            speed = (done - have) / max(time.time() - t0, 0.001)
                            print(tr("    {0} / {1} ({2}/с)", human_size(done), human_size(size) if size else "?", human_size(int(speed))), flush=True)
                            nxt += step
        except (urllib.error.URLError, OSError) as ex:
            if attempt == 2:
                raise NexusError(tr("Завантаження перервано ({0}); повтори — докачається з місця зупинки", ex))
        if not exact or part.stat().st_size >= size:
            break
        print(tr("    з'єднання обірвалось на {0} — докачую", human_size(part.stat().st_size)), flush=True)

def nexus_download(mod: int, file_id: int, archives_root: Path, key: str, game: str = NEXUS_GAME,
                   nxm_key: str | None = None, expires: str | None = None, expect_md5: str | None = None) -> Path | None:
    """Download one mod file into archives_root. Resumes a .part file; checks size (and MD5 when known)."""
    import urllib.request
    import urllib.error
    if game != NEXUS_GAME:
        raise NexusError(tr("Посилання для гри «{0}», а скрипт працює з {1}", game, NEXUS_GAME))
    info = nexus_get(f"/v1/games/{game}/mods/{mod}/files/{file_id}.json", key)
    fname = safe_archive_name(info.get("file_name") or f"{mod}-{file_id}")
    size = int(info.get("size_in_bytes") or 0) or int(info.get("size_kb") or info.get("size") or 0) * 1024
    exact = bool(info.get("size_in_bytes"))
    archives_root.mkdir(parents=True, exist_ok=True)
    dest = archives_root / fname
    if dest.exists() and (not exact or dest.stat().st_size == size) and (not expect_md5 or md5_file(dest) == expect_md5.lower()):
        print(tr("  {0}: уже є в mods-archives", fname))
        return dest
    params = {"key": nxm_key, "expires": expires} if nxm_key and expires else None
    links = nexus_get(f"/v1/games/{game}/mods/{mod}/files/{file_id}/download_link.json", key, params)
    if not links:
        raise NexusError(tr("Nexus не дав посилання на завантаження"))
    url = links[0].get("URI")
    part = archives_root / (fname + ".part")
    print(tr("  Завантаження {0} ({1}) з {2}...", fname, human_size(size) if size else "?", links[0].get("short_name") or "CDN"), flush=True)
    http_download(url, part, size if exact else 0)
    got = part.stat().st_size
    if exact and got != size:
        if got > size:
            part.unlink()
        raise NexusError(tr("Розмір не збігається: {0} замість {1}; повтори завантаження", got, size))
    if expect_md5 and md5_file(part) != expect_md5.lower():
        part.unlink()
        raise NexusError(tr("MD5 не збігається з очікуваним — файл видалено"))
    part.replace(dest)
    print(tr("  Готово: {0}", dest.name), flush=True)
    return dest

def handle_nxm(url: str, archives_root: Path, output_root: Path) -> int:
    """Called by the browser for nxm:// links (often without a terminal): download, log, notify."""
    log = output_root / "nexus_downloads.log"
    output_root.mkdir(parents=True, exist_ok=True)
    acc = nexus_account()
    try:
        if not acc:
            raise NexusError(tr("спершу увійди в Nexus: --nexus-login"))
        if "/collections/" in url:
            data = fetch_collection(url, output_root)
            if not data:
                raise NexusError(tr("колекцію не отримано (див. {0})", log.name))
            msg, ok = tr("Колекцію збережено: {0} модів. Відкрий інтерфейс або запусти --yes, щоб побачити, яких архівів бракує.", len(data["mods"])), True
        else:
            n = parse_nxm(url)
            path = nexus_download(n["mod"], n["file"], archives_root, acc["api_key"], n["game"], n["key"], n["expires"])
            msg, ok = tr("Завантажено: {0}", path.name), True
    except NexusError as ex:
        msg, ok = tr("Nexus: {0}", ex), False
    print(msg)
    with log.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat(timespec='seconds')}\t{'ok' if ok else 'error'}\t{msg}\n")
    if not sys.stdout.isatty():
        notifier = shutil.which("notify-send")
        if notifier:
            subprocess.run([notifier, "-a", APP_NAME, APP_NAME, msg], stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
    return 0 if ok else 1

def nxm_handler_install(script: Path) -> None:
    apps = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    def q(s: str) -> str:  # quoting for the Exec key of .desktop files
        return '"' + re.sub(r'([\\"`$])', r"\\\\\1", s) + '"'
    prev = ""
    if shutil.which("xdg-mime"):
        prev = subprocess.run(["xdg-mime", "query", "default", "x-scheme-handler/nxm"], capture_output=True,
                              text=True, check=False).stdout.strip()
    (apps / NXM_DESKTOP).write_text(
        "[Desktop Entry]\nType=Application\nName=Skyrim Mod Engine (nxm)\nNoDisplay=true\nTerminal=false\n"
        f"Exec={q(sys.executable)} {q(str(script))} --nxm %u\nMimeType=x-scheme-handler/nxm;\n", encoding="utf-8")
    if shutil.which("xdg-mime"):
        subprocess.run(["xdg-mime", "default", NXM_DESKTOP, "x-scheme-handler/nxm"], check=False)
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(apps)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(tr("Обробник nxm:// зареєстровано: {0}", apps / NXM_DESKTOP))
    if prev and prev != NXM_DESKTOP:
        print(tr("  До цього посилання nxm:// відкривав {0} — тепер їх обробляє цей скрипт.", prev))
    print(tr("  На сайті Nexus натискай «Mod Manager Download»: файл ляже в mods-archives, потім «Розпакувати нові»."))

def collection_download(coll: dict, archives_root: Path, output_root: Path, with_optional: bool) -> int:
    """Premium only: download the archives of the collection that are not in mods-archives yet."""
    acc = nexus_account()
    if not acc:
        print(tr("Спершу увійди в Nexus: --nexus-login"))
        return 1
    if not acc.get("is_premium"):
        print(tr("Пряме завантаження доступне лише з Nexus Premium. Без нього відкривай посилання з collection_report.txt і натискай «Mod Manager Download» (потрібен --nxm-handler)."))
        return 1
    match = match_collection(coll, archives_root, output_root)
    todo = [(i, m) for i, m in enumerate(coll["mods"]) if i not in match and (with_optional or not m.get("optional"))]
    print(tr("\n=== Завантаження архівів колекції: {0} ===", len(todo)))
    failed = 0
    for k, (i, m) in enumerate(todo, 1):
        s = m.get("source") or {}
        if not s.get("modId") or not s.get("fileId"):
            print(tr("[{0}/{1}] {2}: немає modId/fileId (не з Nexus) — пропущено", k, len(todo), m.get("name")))
            failed += 1
            continue
        print(f"[{k}/{len(todo)}] {m.get('name')}", flush=True)
        try:
            nexus_download(int(s["modId"]), int(s["fileId"]), archives_root, acc["api_key"],
                           m.get("domainName") or NEXUS_GAME, expect_md5=s.get("md5"))
        except NexusError as ex:
            print(tr("  ПОМИЛКА: {0}", ex))
            failed += 1
    print(tr("Завантажено: {0}, з помилками: {1}", len(todo) - failed, failed))
    return 0 if not failed else 1

# --- Nexus collections: the collection file itself, without Vortex ---------------------------------
COLLECTION_REF_RE = re.compile(r"collections/([A-Za-z0-9]+)(?:/revisions/(\d+))?")

def parse_collection_ref(ref: str) -> tuple[str, int | None]:
    """nxm://<game>/collections/<slug>/revisions/<n>, a collection page link, or the bare slug."""
    ref = ref.strip()
    m = COLLECTION_REF_RE.search(ref)
    if m:
        return m.group(1), int(m.group(2)) if m.group(2) else None
    if re.fullmatch(r"[A-Za-z0-9]{4,}", ref):
        return ref, None
    raise NexusError(tr("Не схоже на посилання на колекцію Nexus: {0}", ref[:120]))

def nexus_graphql(query: str, variables: dict, key: str) -> dict:
    res = nexus_get("/v2/graphql", key, body={"query": query, "variables": variables})
    if res.get("errors"):
        raise NexusError("; ".join(str(e.get("message", e)) for e in res["errors"])[:300])
    return res.get("data") or {}

def nexus_collection_revision(slug: str, revision: int | None, key: str) -> dict:
    """{name, revision, link}. Tries the exact revision first, then the latest published one; the
    adult-content flag is sent where the API accepts it (adult collections are hidden otherwise)."""
    attempts = []
    if revision is not None:
        for adult in (True, False):
            attempts.append(("rev", adult, """query($slug: String!, $rev: Int%s) {
  collectionRevision(slug: $slug, revision: $rev%s) { revisionNumber downloadLink collection { name game { domainName } } }
}""" % ((", $adult: Boolean", ", viewAdultContent: $adult") if adult else ("", ""))))
    for adult in (True, False):
        attempts.append(("latest", adult, """query($slug: String!%s) {
  collection(slug: $slug%s) { name game { domainName } latestPublishedRevision { revisionNumber downloadLink } }
}""" % ((", $adult: Boolean", ", viewAdultContent: $adult") if adult else ("", ""))))
    last, answered = None, False
    for kind, adult, q in attempts:
        v = {"slug": slug}
        if kind == "rev":
            v["rev"] = revision
        if adult:
            v["adult"] = True
        try:
            d = nexus_graphql(q, v, key)
        except NexusError as ex:
            last = ex
            continue
        answered = True  # the API understood the query; an empty result means "no such collection"
        if kind == "rev" and d.get("collectionRevision"):
            r = d["collectionRevision"]
            col = r.get("collection") or {}
            return {"name": col.get("name"), "revision": r.get("revisionNumber"), "link": r.get("downloadLink"),
                    "game": (col.get("game") or {}).get("domainName")}
        if kind == "latest" and d.get("collection"):
            c = d["collection"]
            r = c.get("latestPublishedRevision") or {}
            if revision is not None and r.get("revisionNumber") != revision:
                print(tr("УВАГА: ревізію {0} отримати не вдалося, беру останню опубліковану ({1})", revision, r.get("revisionNumber")))
            return {"name": c.get("name"), "revision": r.get("revisionNumber"), "link": r.get("downloadLink"),
                    "game": (c.get("game") or {}).get("domainName")}
    if answered:
        raise NexusError(tr("Колекцію «{0}» не знайдено (або вона прихована чи видалена)", slug))
    raise NexusError(tr("Nexus не відповів на запит колекції «{0}»: {1}", slug, last))

def fetch_collection(ref: str, output_root: Path, pdir: Path | None = None) -> dict | None:
    """Download the collection bundle from Nexus and make its collection.json the active collection."""
    acc = nexus_account()
    if not acc:
        print(tr("Спершу увійди в Nexus: --nexus-login"))
        return None
    try:
        slug, rev = parse_collection_ref(ref)
        info = nexus_collection_revision(slug, rev, acc["api_key"])
        if info.get("game") and info["game"] != NEXUS_GAME:
            raise NexusError(tr("Колекція для гри «{0}», а скрипт працює з {1}", info["game"], NEXUS_GAME))
        if not info.get("link"):
            raise NexusError(tr("Nexus не дав посилання на файл колекції"))
        print(tr("Колекція «{0}», ревізія {1}: завантажую файл колекції...", info["name"], info["revision"]), flush=True)
        links = nexus_get(info["link"], acc["api_key"])
        if isinstance(links, dict):
            links = links.get("download_links") or links.get("links") or []
        if not links or not links[0].get("URI"):
            raise NexusError(tr("Nexus не дав посилання на файл колекції"))
        bdir = output_root / "collection_bundles"
        bdir.mkdir(parents=True, exist_ok=True)
        bundle = bdir / f"{slug}-r{info['revision']}.7z"
        part = bundle.with_name(bundle.name + ".part")
        if not bundle.exists():
            http_download(links[0]["URI"], part)
            part.replace(bundle)
        staging = bdir / f".unpack-{slug}"
        shutil.rmtree(staging, ignore_errors=True)
        if zipfile.is_zipfile(bundle):
            extract_zip(bundle, staging)
        else:
            tool = choose_tool(bundle, find_tools())
            if not tool:
                raise NexusError(tr("Немає 7z/bsdtar, щоб розпакувати файл колекції"))
            extract_external(bundle, staging, tool)
        found = sorted(staging.rglob("collection.json"), key=lambda p: len(p.parts))
        if not found:
            raise NexusError(tr("У файлі колекції немає collection.json"))
        data = json.loads(found[0].read_text(encoding="utf-8"))
        if not isinstance(data.get("mods"), list):
            raise NexusError(tr("collection.json не схожий на колекцію Vortex (немає списку mods)"))
        pdir = pdir or profile_dir(output_root, read_settings(output_root).get("profile") or DEFAULT_PROFILE)
        pdir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(found[0], pdir / COLLECTION_FILE)
        shutil.rmtree(staging, ignore_errors=True)
    except NexusError as ex:
        print(tr("ПОМИЛКА: {0}", ex))
        return None
    req = sum(1 for m in data["mods"] if not m.get("optional"))
    print(tr("Колекцію збережено: {0} модів (обов'язкових {1}). Вона використовуватиметься в наступних запусках.", len(data["mods"]), req))
    if not acc.get("is_premium"):
        print(tr("Без Premium архіви качаються кнопкою «Mod Manager Download»: список посилань — у collection_report.txt і в GUI (сторінка «Колекція»)."))
    return data

# --- SKSE from Nexus --------------------------------------------------------------------------------
SKSE_NEXUS_MOD = 30379  # Skyrim Script Extender (SKSE64) on Nexus

def skse_target_runtime(game_dir: Path, mods_root: Path) -> tuple[int, ...] | None:
    """The game version SKSE must match; with Skyrim Runtime Swapper, the version it swaps to."""
    exe = ci_join(game_dir, "SkyrimSE.exe")
    gv = pe_file_version(exe) if exe else None
    runtime = gv[:3] if gv else None
    srs = find_srs(game_dir, mods_root)
    if srs and runtime and runtime in (vtuple(srs["sourceVersion"]), vtuple(srs["targetVersion"])):
        runtime = vtuple(srs["targetVersion"])
    return runtime

def archive_names(path: Path) -> list[str]:
    try:
        inv = zip_inventory(path) if zipfile.is_zipfile(path) else external_inventory(path, choose_tool(path, find_tools()) or "")
    except Exception:
        return []
    return [f["path"] for f in inv.get("files", [])]

def skse_install(game_dir: Path, mods_root: Path, archives_root: Path) -> int:
    runtime = skse_target_runtime(game_dir, mods_root)
    if not runtime:
        print(tr("Не вдалося визначити версію гри (SkyrimSE.exe)."))
        return 1
    want = "skse64_{}_{}_{}.dll".format(*runtime)
    rt = ".".join(map(str, runtime))
    print(tr("\n=== SKSE для гри {0} (потрібен {1}) ===", rt, want))
    have = [str(p.relative_to(mods_root)) for p in mods_root.glob("*/*") if p.name.lower() == want] + \
           [str(p.relative_to(mods_root)) for p in mods_root.glob("*/*/*") if p.name.lower() == want]
    if ci_join(game_dir, want) or have:
        print(tr("Потрібний SKSE уже є: {0}", ", ".join(have) or tr("у папці гри")))
        return 0
    acc = nexus_account()
    if not acc:
        print(tr("Спершу увійди в Nexus: --nexus-login"))
        return 1
    try:
        data = nexus_get(f"/v1/games/{NEXUS_GAME}/mods/{SKSE_NEXUS_MOD}/files.json", acc["api_key"])
    except NexusError as ex:
        print(tr("ПОМИЛКА: {0}", ex))
        return 1
    pats = (rt, "_".join(map(str, runtime)), "-".join(map(str, runtime)))
    def score(f):
        text = " ".join(str(f.get(k) or "") for k in ("name", "description", "version", "file_name"))
        return (any(p in text for p in pats), str(f.get("category_name", "")).upper() == "MAIN",
                f.get("uploaded_timestamp") or 0)
    files = sorted((f for f in data.get("files", []) if str(f.get("category_name", "")).upper() not in ("ARCHIVED", "DELETED")),
                   key=score, reverse=True)
    if not files:
        print(tr("На сторінці SKSE на Nexus немає файлів для завантаження."))
        return 1
    if not acc.get("is_premium"):
        f = files[0]
        print(tr("Найімовірніше потрібний файл: «{0}» (версія {1}).", f.get("name"), f.get("version")))
        print(tr("Без Premium: відкрий посилання і натисни «Mod Manager Download» (потрібен --nxm-handler):"))
        print(f"  https://www.nexusmods.com/{NEXUS_GAME}/mods/{SKSE_NEXUS_MOD}?tab=files&file_id={f.get('file_id')}&nmm=1")
        return 1
    for f in files[:4]:
        try:
            path = nexus_download(SKSE_NEXUS_MOD, int(f["file_id"]), archives_root, acc["api_key"])
        except NexusError as ex:
            print(tr("  ПОМИЛКА: {0}", ex))
            continue
        names = [PurePosixPath(n.replace("\\", "/")).name.lower() for n in archive_names(path)]
        if want in names:
            print(tr("SKSE «{0}» підходить до гри {1}. Розпакуй (--yes) і розгорни (--deploy); старий SKSE у modlist.txt краще вимкнути «-».", path.name, rt))
            return 0
        print(tr("  {0} не містить {1} — це не та збірка, видаляю", path.name, want))
        path.unlink()
    print(tr("Не знайшов на Nexus SKSE для гри {0}. Можливо, SKSE ще не оновили під цю версію — тоді краще відкотити гру або взяти SRS.", rt))
    return 1

# ===================================================================================
# GUI module: a local web interface (127.0.0.1 only, random port, secret token) opened in the
# default browser. Started by --gui or by a double-click (no arguments, no terminal). Every real
# operation runs this same script as a subprocess with the same keys as in the console, so the GUI
# and the CLI never disagree. Everything below is used only in GUI mode.
# ===================================================================================
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

GUI_ACTIONS = {
    "scan": ["--yes"],
    "fomod_auto": ["--fomod", "--auto"],
    "fomod_redo_auto": ["--fomod-redo", "--auto"],
    "conflicts": ["--conflicts"],
    "dry": ["--deploy", "--dry-run"],
    "deploy": ["--deploy"],
    "deploy_launch": ["--deploy"],
    "undeploy": ["--undeploy"],
    "skse": ["--skse"],
    "loot_update": ["--deploy", "--dry-run", "--loot-update"],
    "nxm_handler": ["--nxm-handler"],
    "skse_install": ["--skse-install", "--yes"],
    "game_check": ["--game-check"],
    "incremental_on": ["--incremental-backup", "on"],
    "incremental_off": ["--incremental-backup", "off"],
    "game_repair": ["--game-repair"],
    "game_baseline": ["--game-baseline"],
    "collection_download": ["--collection-download", "--yes"],
    "collection_download_all": ["--collection-download", "--with-optional", "--yes"],
}
GUI_VALUE_ACTIONS = {  # action -> (extra keys, key that takes the value from the page)
    "profile": (["--scan-only"], "--profile"), "profile_new": (["--scan-only"], "--profile-new"),
    "profile_delete": (["--scan-only"], "--profile-delete"), "lang": (["--scan-only"], "--lang"),
    "nexus_download": (["--yes"], "--nexus-download"), "collection_nexus": (["--yes"], "--collection-nexus"),
    "backup_full": (["--backup-mode", "full"], "--backup-game"), "backup_link": (["--backup-mode", "link"], "--backup-game"),
    "backup_full_saves": (["--backup-mode", "full", "--with-saves"], "--backup-game"),
    "restore_game": ([], "--restore-game"),
    "set_archives": (["--scan-only"], "--set-archives"), "set_archives_move": (["--move", "--scan-only"], "--set-archives"),
    "set_mods": (["--scan-only"], "--set-mods"), "set_mods_move": (["--move", "--scan-only"], "--set-mods"), "backup_verify": ([], "--backup-verify"), "backup_delete": ([], "--backup-delete"),
}
GUI_CATEGORY_RULES = (  # dominant file type -> category key
    ("Animation/behavior", "anim"), ("Flash/UI asset", "ui"), ("Audio", "audio"),
    ("Texture", "gfx"), ("3D model", "gfx"), ("Papyrus script", "scripts"),
    ("Configuration", "config"), ("JSON data", "config"),
)


def gui_archive_meta(archive_name: str) -> dict:
    """Nexus file names look like "Name-<modid>-<version parts>-<upload time>.ext"."""
    stem = re.sub(r"(\.tar)?\.[A-Za-z0-9]+$", "", archive_name)
    parts = stem.split("-")
    if len(parts) < 3 or not re.fullmatch(r"\d{9,11}", parts[-1]):
        return {}
    for i in range(1, len(parts) - 1):
        if re.fullmatch(r"\d+", parts[i].strip()):
            ver = ".".join(p for p in parts[i + 1:-1] if p)
            return {"nexus_id": int(parts[i]), "version": ver or None}
    return {}


def gui_mod_category(s: dict) -> str:
    if s.get("skse_plugins"):
        return "skse"
    if s.get("layout") == "root-only":
        return "root"
    counts = s.get("counts") or {}
    if s.get("plugins") and not any(counts.get(k, 0) > 20 for k in ("Texture", "3D model", "Audio", "Animation/behavior")):
        return "plugin"
    best = max(counts.items(), key=lambda kv: kv[1], default=(None, 0))[0]
    for key, cat in GUI_CATEGORY_RULES:
        if best == key:
            return cat
    return "plugin" if s.get("plugins") else "other"


class GuiApp:
    """State and actions behind the graphical interface (engine = this module)."""
    def __init__(self, engine, engine_path: Path, output: str):
        self.e = engine
        self.engine_path = engine_path
        self.root = engine_path.parent
        self.out = (self.root / output).resolve()
        self.token = secrets.token_urlsafe(18)
        self.lock = threading.Lock()
        self.job: dict | None = None
        self.log: list[str] = []
        self.log_base = 0
        self.last_beat = 0.0
        self.started = time.time()
        self.stop = threading.Event()

    # ------------------------------------------------------------------ state
    def profile(self) -> str:
        e = self.e
        p = e.read_settings(self.out).get("profile") or e.DEFAULT_PROFILE
        return p if p in e.list_profiles(self.out) else e.DEFAULT_PROFILE

    def read_json(self, name: str):
        try:
            return json.loads((self.out / name).read_text(encoding="utf-8"))
        except Exception:
            return None

    def state(self) -> dict:
        e = self.e
        if (self.out / "modlist.txt").exists() or not e.profiles_root(self.out).exists():
            e.migrate_to_profiles(self.out)
        profile = self.profile()
        pdir = e.profile_dir(self.out, profile)
        manifest = self.read_json("manifest.json") or {}
        summaries = manifest.get("mods", [])
        names = {s["name"] for s in summaries}
        raw_fomods = {s["name"] for s in summaries if s["layout"] == "fomod"}
        owned = e.fomod_owned(pdir, raw_fomods, names)
        view = e.profile_view(summaries, owned)
        deployable = e.deployable(view)
        dep_names = {s["name"] for s in deployable}

        # Order and enabled state straight from the profile's modlist.txt (edited by the GUI).
        order, disabled = [], set()
        ml = pdir / "modlist.txt"
        if ml.exists():
            for line in ml.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    n, on = e.parse_modlist_line(line, dep_names)
                    if n and n not in order:
                        order.append(n)
                        if not on:
                            disabled.add(n)
        missing = sorted((n for n in dep_names if n not in order), key=str.lower)
        order += missing
        if ml.exists():
            disabled |= set(missing)  # as the next scan will list them: off in this profile

        # Archive name -> version / Nexus id for each mod folder.
        meta: dict[str, dict] = {}
        for rel, rec in (self.read_json("processed_archives.json") or {}).items():
            dest = rec.get("destination")
            if dest:
                meta[Path(dest).name] = dict(gui_archive_meta(Path(rel).name), archive=Path(rel).name)
        deps = manifest.get("mod_deps") or {}
        rdeps: dict[str, list[str]] = {}
        for n, ds in deps.items():
            for d in ds:
                rdeps.setdefault(d, []).append(n)

        conf = self.read_json("conflicts.json") or {}
        wins: dict[str, list] = {}
        loses: dict[str, list] = {}
        for p in conf.get("pairs", []):
            wins.setdefault(p["winner"], []).append([p["loser"], p["count"]])
            loses.setdefault(p["loser"], []).append([p["winner"], p["count"]])

        choices = e.fomod_choice_records(pdir)
        by_name = {s["name"]: s for s in summaries}
        mods = []
        for idx, n in enumerate(order):
            s = by_name.get(n)
            if not s:
                continue
            m = e.FOMOD_OUT_RE.match(n)
            base = m.group(1) if m and m.group(1) in raw_fomods else None
            md = meta.get(base or n, {})
            rec = choices.get(base) if base else None
            mods.append({
                "name": n, "index": idx, "enabled": n not in disabled, "layout": s["layout"],
                "category": gui_mod_category(s), "size": s.get("size"), "size_bytes": s.get("size_bytes", 0),
                "files": s.get("file_count", 0), "plugins": s.get("plugins", []),
                "skse_plugins": s.get("skse_plugins", []), "bsa": s.get("bsa", []),
                "version": md.get("version"), "nexus_id": md.get("nexus_id"), "archive": md.get("archive"),
                "deps": deps.get(n, []), "rdeps": sorted(rdeps.get(n, [])),
                "wins": wins.get(n, []), "loses": loses.get(n, []),
                "fomod_of": base, "fomod_steps": rec.get("steps") if rec else None,
            })
        attention = [{"name": s["name"], "layout": s["layout"], "options": s.get("options", [])}
                     for s in view if s["name"] not in dep_names and s["layout"] != "fomod"]
        fomods = []
        for raw in sorted(raw_fomods, key=str.lower):
            rec = choices.get(raw)
            fomods.append({"name": raw, "installed_to": owned.get(raw),
                           "mode": rec.get("mode") if rec else None,
                           "steps": rec.get("steps") if rec else None,
                           "file_count": rec.get("file_count") if rec else None})

        journal = e.load_journal(self.out)
        deployed = None
        pending = False
        if journal:
            deployed = {"profile": journal.get("profile", e.DEFAULT_PROFILE), "at": journal.get("at"),
                        "files": len(journal.get("planned", journal.get("files", []))),
                        "complete": bool(journal.get("complete", True)), "plugins": journal.get("plugins", []),
                        "game_dir": journal.get("game_dir")}
            if deployed["profile"] == profile:
                jorder = [x for x in journal.get("order", []) if x != e.OVERWRITE_NAME]
                pending = jorder != [m["name"] for m in mods if m["enabled"]]
        ow = pdir / "overwrite"
        ow_files = sorted(p.relative_to(ow).as_posix() for p in ow.rglob("*") if p.is_file()) if ow.is_dir() else []
        bdir = self.out / "backup" / "files"
        backups = sum(1 for p in bdir.rglob("*") if p.is_file()) if bdir.is_dir() else 0
        changed_dir = self.out / "deploy_changed"
        changed = sorted((d.name for d in changed_dir.iterdir() if d.is_dir()), reverse=True) if changed_dir.is_dir() else []
        skse = self.read_json("skse_report.json")
        lang = str(e.read_settings(self.out).get("lang") or e._LANG)
        if lang in e.LANGS:
            e._LANG = lang  # the engine's tr() follows a language change made from the page
        with self.lock:
            job = dict(self.job) if self.job else None
        return {
            "lang": e._LANG, "version": manifest.get("engine_version") or "?",
            "catalog_at": manifest.get("generated_at"),
            "profile": profile, "profiles": e.list_profiles(self.out),
            "mods": mods, "attention": attention, "fomods": fomods,
            "problems": manifest.get("order_problems", []) if manifest.get("profile") == profile else [],
            "conflicts": {"computed": bool(conf), "pairs": conf.get("pairs", [])[:300],
                          "pairs_total": len(conf.get("pairs", [])), "duplicate_plugins": conf.get("duplicate_plugins", []),
                          "identical": (lambda v: v if isinstance(v, int) else len(v or []))(conf.get("identical_files"))},
            "deployed": deployed, "pending": pending,
            "overwrite": {"files": ow_files[:500], "total": len(ow_files)},
            "backups": {"files": backups, "changed_sets": changed[:20]},
            "skse": skse, "job": job,
            "nexus": ({"name": acc.get("name"), "premium": bool(acc.get("is_premium"))} if (acc := e.nexus_account()) else None),
            "collection": self.collection_state(),
            "game_backups": e.list_backups(self.out),
            "protection": dict(zip(("store_files", "store_size"), e.store_stats(self.out)),
                               incremental=e.incremental_enabled(self.out),
                               asked=bool(e.read_settings(self.out).get("protection_asked"))),
            "baseline": ({"version": b.get("game_version"), "created": b.get("created"), "files": len(b.get("files", {}))}
                         if (b := e.load_baseline(self.out)) else None),
            "paths": dict(zip(("archives", "mods"), map(str, e.configured_dirs(self.root, self.out))),
                          root=str(self.root), output=str(self.out), profile=str(pdir)),
        }

    def collection_state(self) -> dict | None:
        """The active collection and which of its archives are present (MD5 cache only: no hashing here)."""
        self.e.migrate_collection(self.out)
        try:
            c = json.loads((self.e.profile_dir(self.out, self.profile()) / self.e.COLLECTION_FILE).read_text(encoding="utf-8"))
        except Exception:
            c = None
        if not c or not isinstance(c.get("mods"), list):
            return None
        have = {v.get("md5") for v in (self.read_json("md5_cache.json") or {}).values()}
        mods = []
        for m in c["mods"]:
            src = m.get("source") or {}
            mods.append({"name": m.get("name"), "version": m.get("version"), "optional": bool(m.get("optional")),
                         "mod_id": src.get("modId"), "file_id": src.get("fileId"),
                         "have": bool(src.get("md5")) and src.get("md5", "").lower() in have})
        info = c.get("info") or {}
        return {"name": info.get("name"), "author": info.get("author"), "mods": mods}

    def set_lang(self, code: str) -> None:
        """Interface language only: a setting, no rescan."""
        if code in self.e.LANGS:
            self.e.update_settings(self.out, lang=code)
            self.e._LANG = code

    def set_profile(self, name: str) -> str | None:
        """Switch the active profile without a rescan; the next scan/deploy brings its list up to date."""
        if self.job and self.job.get("running"):
            return "busy"
        if name not in self.e.list_profiles(self.out):
            return "unknown profile"
        self.e.update_settings(self.out, profile=name)
        return None

    def new_profile_from_collection(self, name: str, source: str) -> str | None:
        """source: a collection.json path, or a Nexus collection link / nxm:// / code."""
        src = source.strip()
        nexus = src.startswith(("http", "nxm:")) or (not Path(src).expanduser().exists() and re.fullmatch(r"[A-Za-z0-9]{4,}", src))
        GUI_ACTIONS["profile_new_coll"] = []
        return self.start("profile_new_coll", ["--profile-new", name] + (["--collection-nexus", src] if nexus else ["--collection", src]) + ["--scan-only"])

    # ------------------------------------------------------------------ edits
    def edit_modlist(self, fn) -> None:
        e = self.e
        pdir = e.profile_dir(self.out, self.profile())
        st = self.state()
        order = [m["name"] for m in st["mods"]]
        disabled = {m["name"] for m in st["mods"] if not m["enabled"]}
        order, disabled = fn(order, disabled)
        e.write_modlist(pdir / "modlist.txt", order, disabled)

    def toggle(self, name: str, enabled: bool) -> None:
        def f(order, disabled):
            (disabled.discard if enabled else disabled.add)(name)
            return order, disabled
        self.edit_modlist(f)

    def move(self, name: str, to: int) -> None:
        def f(order, disabled):
            if name in order:
                order.remove(name)
                order.insert(max(0, min(to, len(order))), name)
            return order, disabled
        self.edit_modlist(f)

    # ------------------------------------------------------------------ jobs
    def start(self, action: str, extra: list[str] | None = None) -> str | None:
        with self.lock:
            if self.job and self.job.get("running"):
                return "busy"
            args = list(GUI_ACTIONS.get(action, [])) + list(extra or [])
            if not args:
                return "unknown action"
            self.job = {"action": action, "args": args, "running": True, "rc": None, "started": time.time()}
            self.log_base += len(self.log)
            self.log = []
        threading.Thread(target=self._run, args=(action, args), daemon=True).start()
        return None

    def _run(self, action: str, args: list[str]) -> None:
        env = dict(os.environ, PYTHONUNBUFFERED="1", SME_GUI="1")
        cmd = [sys.executable, str(self.engine_path)] + args
        self._append("$ " + " ".join(["python3", self.engine_path.name] + args))
        try:
            proc = subprocess.Popen(cmd, cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", env=env)
            for line in proc.stdout:
                self._append(line.rstrip("\n"))
            rc = proc.wait()
        except Exception as ex:
            self._append(f"ERROR: {ex}")
            rc = -1
        with self.lock:
            self.job.update(running=False, rc=rc, finished=time.time())
        if action == "deploy_launch" and rc == 0 and not self._deploy_failed():
            self.launch()

    def _deploy_failed(self) -> bool:
        j = self.e.load_journal(self.out)
        return not (j and j.get("complete"))

    def _append(self, line: str) -> None:
        with self.lock:
            self.log.append(line)
            if len(self.log) > 8000:
                cut = len(self.log) - 6000
                self.log = self.log[cut:]
                self.log_base += cut

    def log_since(self, n: int) -> dict:
        with self.lock:
            start = max(0, n - self.log_base)
            return {"lines": self.log[start:], "next": self.log_base + len(self.log),
                    "job": dict(self.job) if self.job else None}

    def launch(self) -> str:
        if self.e.game_running():
            return "running"
        url = f"steam://rungameid/{SKYRIM_APPID}"
        for cmd in ([shutil.which("steam"), "-applaunch", SKYRIM_APPID] if shutil.which("steam") else None,
                    [shutil.which("xdg-open"), url] if shutil.which("xdg-open") else None):
            if cmd:
                try:
                    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
                    self._append(self.e.tr("Запуск гри через Steam..."))
                    return "ok"
                except OSError:
                    continue
        return "no-steam"

    def open_path(self, what: str) -> str:
        a_dir, m_dir = self.e.configured_dirs(self.root, self.out)
        st_paths = {"archives": a_dir, "mods": m_dir,
                    "output": self.out, "profile": self.e.profile_dir(self.out, self.profile()),
                    "overwrite": self.e.profile_dir(self.out, self.profile()) / "overwrite"}
        p = st_paths.get(what)
        if what.startswith("mod:"):
            name = what[4:]
            p = m_dir / name
            if p.parent != m_dir:
                return "bad"
        if p is None:
            return "bad"
        p.mkdir(parents=True, exist_ok=True)
        opener = shutil.which("xdg-open")
        if not opener:
            return "no-opener"
        subprocess.Popen([opener, str(p)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return "ok"


def gui_make_handler(app: GuiApp):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code: int = 200) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

        def _ok_origin(self) -> bool:
            host = (self.headers.get("Host") or "").split(":")[0]
            return host in ("127.0.0.1", "localhost")

        def _authed(self, q: dict) -> bool:
            return self._ok_origin() and (self.headers.get("X-Token") == app.token or q.get("t", [""])[0] == app.token)

        def _safe(self, fn):
            """Any error becomes a readable 500 answer (and gui_error.log) instead of a dropped connection."""
            try:
                fn()
            except Exception:
                import traceback
                tb = traceback.format_exc()
                try:
                    with (app.out / "gui_error.log").open("a", encoding="utf-8") as f:
                        f.write(f"--- {datetime.now().isoformat(timespec='seconds')} {self.command} {self.path.split('?')[0]}\n{tb}\n")
                except OSError:
                    pass
                try:
                    self._json({"error": "internal", "detail": tb[-2000:]}, 500)
                except Exception:
                    pass

        def do_GET(self):
            self._safe(self._get)

        def do_POST(self):
            self._safe(self._post)

        def _get(self):
            u = urlparse(self.path)
            if u.path == "/favicon.ico":
                return self._send(204, b"", "image/x-icon")
            q = parse_qs(u.query)
            if not self._authed(q):
                return self._send(403, b"forbidden", "text/plain")
            if u.path == "/":
                page = GUI_PAGE.replace("__TOKEN__", app.token)
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            if u.path == "/api/state":
                return self._json(app.state())
            if u.path == "/api/log":
                return self._json(app.log_since(int(q.get("from", ["0"])[0] or 0)))
            self._send(404, b"not found", "text/plain")

        def _post(self):
            u = urlparse(self.path)
            if not self._authed(parse_qs(u.query)):
                return self._send(403, b"forbidden", "text/plain")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._json({"error": "bad json"}, 400)
            p = u.path
            busy = app.job and app.job.get("running")
            if p == "/api/beat":
                app.last_beat = time.time()
                return self._json({"ok": True})
            if p == "/api/quit":
                app.stop.set()
                return self._json({"ok": True})
            if p in ("/api/toggle", "/api/move") and busy:
                return self._json({"error": "busy"}, 409)
            if p == "/api/toggle":
                app.toggle(str(body["name"]), bool(body["enabled"]))
                return self._json({"ok": True})
            if p == "/api/move":
                app.move(str(body["name"]), int(body["to"]))
                return self._json({"ok": True})
            if p == "/api/run":
                action = str(body.get("action"))
                extra = []
                if action in GUI_VALUE_ACTIONS:
                    base, flag = GUI_VALUE_ACTIONS[action]
                    val = str(body.get("value") or "")
                    extra = [flag, val] + base
                    action_key = action
                    GUI_ACTIONS[action_key] = []
                err = app.start(action, extra)
                return self._json({"error": err} if err else {"ok": True}, 409 if err else 200)
            if p == "/api/nexus_login":
                acc = app.e.nexus_login(str(body.get("key") or "").strip() or None)
                return self._json({"ok": bool(acc)} if acc else {"error": "login"}, 200 if acc else 400)
            if p == "/api/nexus_logout":
                app.e.nexus_logout()
                return self._json({"ok": True})
            if p == "/api/protection":
                if busy:
                    return self._json({"error": "busy"}, 409)
                app.e.update_settings(app.out, protection_asked=True, incremental_backup=bool(body.get("incremental")))
                args = (["--game-baseline"] if body.get("baseline") else []) + \
                    (["--backup-game", "", "--backup-mode", body["backup"]] if body.get("backup") in ("full", "link") else [])
                if not args:
                    return self._json({"ok": True})
                GUI_ACTIONS["protection"] = []
                err = app.start("protection", args)
                return self._json({"error": err} if err else {"ok": True, "job": True}, 409 if err else 200)
            if p == "/api/set":
                if body.get("lang"):
                    app.set_lang(str(body["lang"]))
                if body.get("profile"):
                    err = app.set_profile(str(body["profile"]))
                    if err:
                        return self._json({"error": err}, 409)
                return self._json({"ok": True})
            if p == "/api/profile_new_coll":
                err = app.new_profile_from_collection(str(body.get("name") or ""), str(body.get("source") or ""))
                return self._json({"error": err} if err else {"ok": True}, 409 if err else 200)
            if p == "/api/launch":
                return self._json({"result": app.launch()})
            if p == "/api/open":
                return self._json({"result": app.open_path(str(body.get("what")))})
            self._send(404, b"not found", "text/plain")
    return H


def gui_open_browser(url: str, out: Path) -> bool:
    try:
        (out / "gui_url.txt").write_text(url + "\n", encoding="utf-8")
    except OSError:
        pass
    try:
        if webbrowser.open(url):
            return True
    except Exception:
        pass
    if shutil.which("xdg-open"):
        try:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            return True
        except OSError:
            pass
    return False


def gui_run(engine, engine_path: Path, output: str = "mod_engine_output", open_page: bool = True) -> int:
    os.chdir(engine_path.parent)  # the engine keeps Mods/, mods-archives/ and its output next to itself
    app = GuiApp(engine, engine_path, output)
    app.out.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("SME_GUI_PORT", "0"))), gui_make_handler(app))
    url = f"http://127.0.0.1:{srv.server_address[1]}/?t={app.token}"
    print(engine.tr("Графічний інтерфейс: {0}", url), flush=True)
    if open_page and not gui_open_browser(url, app.out):
        msg = engine.tr("Не вдалося відкрити браузер. Відкрий цю адресу вручну:\n{0}", url)
        if shutil.which("kdialog"):
            subprocess.Popen(["kdialog", "--msgbox", msg], start_new_session=True)
        print(msg, flush=True)

    if not engine.load_baseline(app.out) and engine.find_game_dir(None):
        engine.GUI_ACTIONS["baseline_auto"] = ["--game-baseline"]
        app.start("baseline_auto")  # the automatic file list, shown live in the event log

    def watchdog():
        # Stop when the page is gone: no heartbeat for 60 s (or none at all for 10 min), no job running.
        while not app.stop.wait(5):
            idle = time.time() - (app.last_beat or app.started)
            limit = 60 if app.last_beat else 600
            if idle > limit and not (app.job and app.job.get("running")):
                app.stop.set()
        srv.shutdown()
    threading.Thread(target=watchdog, daemon=True).start()
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    srv.server_close()
    return 0


GUI_PAGE = r"""<!doctype html>
<html lang="uk"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Skyrim Mod Engine</title>
<style>
:root{--bg:#0b1220;--panel:#111a2b;--panel2:#152035;--line:#22314c;--text:#e6ecf5;--mut:#8a9ab5;--acc:#2f7fd4;--acc2:#3b93ee;
--ok:#1fbf73;--warn:#e0a43a;--bad:#e2554f;--chip:#1c2a44}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:var(--bg);color:var(--text);
font:14px/1.4 system-ui,-apple-system,"Segoe UI","Noto Sans",Ubuntu,sans-serif}
button,select,input{font:inherit;color:inherit}
.app{display:grid;grid-template-columns:230px 1fr;grid-template-rows:auto 1fr auto;height:100vh}
/* header */
.hdr{grid-column:1/3;position:relative;height:110px;overflow:hidden;border-bottom:1px solid var(--line)}
.hdr svg.bgart{position:absolute;inset:0;width:100%;height:100%}
.brand{position:absolute;left:22px;top:18px;display:flex;gap:14px;align-items:center}
.brand .t1{font-size:24px;font-weight:600;letter-spacing:.2px}.brand .t2{color:#b8c6dd;font-size:14px}
.tag{position:absolute;left:340px;top:30px;color:#cfdbee;font-size:15px;text-shadow:0 1px 3px #000}
.top-right{position:absolute;right:14px;top:12px;display:flex;gap:10px;align-items:center;background:#0c1424cc;
border:1px solid var(--line);border-radius:10px;padding:6px 12px;color:var(--mut);font-size:13px}
/* sidebar */
.side{background:#0d1626;border-right:1px solid var(--line);padding:12px 10px;display:flex;flex-direction:column;gap:2px;overflow:auto}
.nav{display:flex;align-items:center;gap:12px;padding:10px 12px;border-radius:8px;color:#c9d5e8;cursor:pointer;border:0;background:none;text-align:left;width:100%}
.nav:hover{background:#15223a}.nav.on{background:var(--acc);color:#fff}
.nav .cnt{margin-left:auto;background:#1b2a45;border-radius:10px;padding:1px 8px;font-size:12px;color:#b9c7de}
.nav.on .cnt{background:#ffffff33;color:#fff}.nav .cnt.bad{background:#4a1f25;color:#ff9a94}
.nav svg{width:18px;height:18px;flex:none}
.side .about{margin-top:auto;border:1px solid var(--line);border-radius:10px;padding:12px;color:var(--mut);font-size:12.5px;background:var(--panel)}
.side .about b{color:var(--text);font-size:14px;display:block}
/* main */
.main{display:grid;grid-template-columns:1fr 300px;gap:14px;padding:14px;min-height:0;overflow:auto}
.col{display:flex;flex-direction:column;gap:12px;min-height:0}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px}
.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.btn{display:inline-flex;align-items:center;gap:8px;border:1px solid var(--line);background:var(--panel2);padding:9px 14px;border-radius:8px;cursor:pointer}
.btn:hover{border-color:#36507a}.btn.pri{background:var(--acc);border-color:var(--acc)}.btn.pri:hover{background:var(--acc2)}
.btn.go{background:var(--ok);border-color:var(--ok);color:#04150c;font-weight:600}.btn.danger{border-color:#5a2a2e;color:#ffb3ae}
.btn:disabled{opacity:.5;cursor:default}
a.btn{color:inherit;text-decoration:none}
.search{flex:1;min-width:160px;display:flex;align-items:center;gap:8px;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:0 10px}
.search input{flex:1;background:none;border:0;outline:0;padding:9px 0}
select{background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:9px 10px}
.tablewrap{flex:1;min-height:240px;overflow:auto}
table{width:100%;border-collapse:collapse}
th{position:sticky;top:0;background:var(--panel);text-align:left;font-weight:500;color:var(--mut);padding:10px 12px;border-bottom:1px solid var(--line);z-index:1}
td{padding:8px 12px;border-bottom:1px solid #1a2740;vertical-align:middle}
tr.row{cursor:pointer}tr.row:hover{background:#142038}tr.row.sel{background:#17284a}
tr.drag-over td{border-top:2px solid var(--acc2)}
.mname{display:flex;gap:12px;align-items:center}.mname .n{font-weight:500}.mname .s{color:var(--mut);font-size:12.5px}
.av{width:38px;height:38px;border-radius:8px;display:grid;place-items:center;font-weight:700;color:#fff;flex:none;font-size:15px}
.chip{display:inline-block;padding:2px 8px;border-radius:6px;font-size:12.5px;background:var(--chip);border:1px solid #2a3e62}
.c-skse{color:#ffcf7a;border-color:#5b4a26}.c-ui{color:#8cc5ff}.c-gfx{color:#9fe3a0;border-color:#2c5536}.c-audio{color:#e7a6ff}
.c-anim{color:#ffb38a}.c-plugin{color:#cfd8e8}.c-root{color:#ff9d9d}.c-scripts{color:#a7b8ff}.c-config{color:#c2c9d6}
.sw{position:relative;width:38px;height:21px;border-radius:12px;background:#33415c;border:0;cursor:pointer;flex:none}
.sw::after{content:"";position:absolute;left:3px;top:3px;width:15px;height:15px;border-radius:50%;background:#dfe7f3;transition:.15s}
.sw.on{background:var(--ok)}.sw.on::after{left:20px}
.st{display:flex;align-items:center;gap:10px}.st .on{color:var(--ok)}.st .off{color:var(--mut)}
.dots{border:0;background:none;color:var(--mut);cursor:pointer;font-size:18px;padding:2px 8px;border-radius:6px}.dots:hover{background:#1d2c48;color:#fff}
.menu{position:fixed;z-index:20;background:#16233b;border:1px solid #2b3f63;border-radius:8px;padding:4px;min-width:200px;box-shadow:0 10px 30px #0008}
.menu button{display:block;width:100%;text-align:left;border:0;background:none;padding:8px 10px;border-radius:6px;cursor:pointer}
.menu button:hover{background:#21345a}
/* right */
.status{padding:14px}.status h3{margin:0 0 10px;font-size:16px;display:flex;align-items:center;gap:8px}
.kv{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;font-size:13px}.kv .k{color:var(--mut)}
.launch{display:flex;margin-top:12px}.launch .btn.go{flex:1;justify-content:center;border-radius:8px 0 0 8px;padding:11px}
.launch .btn.more{border-radius:0 8px 8px 0;background:#18a463;border-color:#18a463;color:#04150c;padding:0 12px}
.detail{padding:0;overflow:auto;flex:1;min-height:0}
.dhead{padding:14px;display:flex;gap:12px;align-items:center;border-bottom:1px solid var(--line);
background:linear-gradient(135deg,#1a2c4d,#101a2c)}
.dhead .n{font-weight:600;font-size:15px;word-break:break-word}.dhead .v{color:var(--mut);font-size:12.5px}
.dsec{padding:12px 14px;border-bottom:1px solid var(--line);font-size:13px}
.dsec h4{margin:0 0 8px;font-size:13.5px;font-weight:600}
.dsec ul{margin:0;padding-left:18px}.dsec li{margin:2px 0;word-break:break-word}
.link{color:#6fb2ff;text-decoration:none;cursor:pointer}.link:hover{text-decoration:underline}
.okline{color:var(--ok)}.warnline{color:var(--warn)}.badline{color:var(--bad)}
.banner{padding:10px 14px;border-radius:10px;background:#2a2412;border:1px solid #5a4a1d;color:#ffd987;display:flex;gap:10px;align-items:center}
/* log */
.log{grid-column:1/3;display:grid;grid-template-columns:150px 1fr auto;border-top:1px solid var(--line);background:#0c1424;height:120px}
.log .lt{display:flex;gap:10px;align-items:flex-start;padding:12px;font-weight:600}
.log .ico{width:34px;height:34px;border-radius:7px;background:var(--acc);display:grid;place-items:center;font-family:monospace}
.log pre{margin:0;padding:10px 12px;overflow:auto;font:12px/1.45 ui-monospace,"DejaVu Sans Mono",monospace;color:#c4d0e3;border-left:1px solid var(--line);white-space:pre-wrap}
.log .rt{display:flex;align-items:center;gap:10px;padding:0 14px;color:var(--mut)}
.dot{width:9px;height:9px;border-radius:50%;background:var(--ok);display:inline-block}.dot.run{background:var(--warn);animation:p 1s infinite}
@keyframes p{50%{opacity:.3}}
.page{padding:16px;overflow:auto;flex:1;min-height:0}.page h2{margin:0 0 6px;font-size:18px}.page p.m{color:var(--mut);margin:0 0 14px}
.grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.box{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px}
.mono{font-family:ui-monospace,"DejaVu Sans Mono",monospace;font-size:12.5px}
.empty{color:var(--mut);padding:30px;text-align:center}
.search{min-width:0}.search input{min-width:0;width:100%}th.c-num{width:44px}
.mname>div{min-width:0}.mname .n{overflow-wrap:anywhere}
/* Narrower windows (browser side panels, desktop scaling): shrink step by step, never cut content */
@media (max-width:1240px){.app{grid-template-columns:64px 1fr}.nav{justify-content:center;padding:9px 0}.nav span,.nav .cnt,.side .about{display:none}
 .nav .cnt.bad{display:block;position:absolute;margin:-18px 0 0 22px;padding:0 5px;font-size:10px}.nav{position:relative}
 .main{grid-template-columns:1fr 270px}.tag{display:none}.log{grid-template-columns:56px 1fr auto}.log .lt>div:last-child{display:none}}
@media (max-width:1040px){.c-ver{display:none}}
@media (max-width:960px){.main{grid-template-columns:1fr}.rightcol{order:-1}.rightcol .detail{display:none}
 .status .kv{grid-template-columns:auto 1fr auto 1fr}.top-right span{display:none}}
@media (max-width:760px){.c-cat{display:none}.status .kv{grid-template-columns:auto 1fr}.mname .s{display:none}}
/* Steam Deck: 1280x800 minus the browser's own bars leaves ~650-700 px of height */
@media (max-height:860px){.hdr{height:58px}.brand{top:8px}.brand svg{width:38px;height:32px}.brand .t1{font-size:19px}.brand .t2{display:none}
 .tag{top:18px;font-size:13px}.top-right{top:8px;padding:3px 10px}.log{height:84px}.log .lt{padding:8px}.nav{padding:7px 10px}
 .main{padding:10px;gap:10px}.status{padding:10px}.launch{margin-top:8px}td{padding:5px 10px}.av{width:30px;height:30px;font-size:13px}.side .about{display:none}}
@media (max-height:560px){.hdr{display:none}.log{height:64px}}
.log.min{height:34px!important}.log.min pre{display:none}
.logtoggle{border:0;background:none;color:var(--mut);cursor:pointer;font-size:14px}

</style></head>
<body>
<div class="app">
 <div class="hdr">
  <svg class="bgart" viewBox="0 0 1600 150" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
   <defs><linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2a4f86"/><stop offset=".7" stop-color="#4f82b8"/><stop offset="1" stop-color="#8db6d9"/></linearGradient>
   <linearGradient id="fade" x1="0" x2="1"><stop offset="0" stop-color="#0b1220" stop-opacity=".97"/><stop offset=".38" stop-color="#0b1220" stop-opacity=".55"/><stop offset=".75" stop-color="#0b1220" stop-opacity=".1"/><stop offset="1" stop-color="#0b1220" stop-opacity=".45"/></linearGradient></defs>
   <rect width="1600" height="150" fill="url(#sky)"/>
   <g fill="#e9f1fb" opacity=".55"><ellipse cx="980" cy="38" rx="90" ry="10"/><ellipse cx="1240" cy="24" rx="70" ry="8"/><ellipse cx="720" cy="56" rx="60" ry="7"/></g>
   <path d="M0 120 L120 70 L210 105 L330 40 L430 95 L560 30 L690 100 L800 50 L930 110 L1040 35 L1150 95 L1270 45 L1380 100 L1490 60 L1600 90 L1600 150 L0 150Z" fill="#6c86a8"/>
   <path d="M330 40 L355 58 L345 60 L370 75 L300 66Z M560 30 L590 52 L575 54 L600 70 L530 60Z M1040 35 L1068 55 L1055 57 L1080 72 L1012 62Z M1270 45 L1295 62 L1283 63 L1305 78 L1245 70Z" fill="#eef4fb" opacity=".9"/>
   <path d="M0 135 L150 100 L280 125 L420 90 L560 128 L700 95 L860 130 L980 100 L1120 132 L1260 98 L1400 128 L1600 105 L1600 150 L0 150Z" fill="#2e4766"/>
   <g fill="#1d3048"><path d="M1110 150 L1110 96 L1118 96 L1118 84 L1124 72 L1130 84 L1130 96 L1150 96 L1150 78 L1157 64 L1164 78 L1164 96 L1184 96 L1184 86 L1190 76 L1196 86 L1196 150Z"/>
   <path d="M1352 26 l20 -8 l20 8 l-8 22 l-24 0z" opacity=".8"/><path d="M842 40 l14 -6 l14 6 l-6 15 l-16 0z" opacity=".7"/></g>
   <g fill="#16263b">%TREES%</g>
   <rect width="1600" height="150" fill="url(#fade)"/>
  </svg>
  <div class="brand"><svg width="54" height="46" viewBox="0 0 54 46"><path d="M3 42 L20 10 L27 22 L33 12 L51 42Z" fill="none" stroke="#e8eef8" stroke-width="2.6" stroke-linejoin="round"/><path d="M20 10 L24 18 L17 18Z M33 12 L37 20 L30 19Z" fill="#e8eef8"/><path d="M27 46 L22 38 L32 38Z" fill="#e8eef8"/></svg>
   <div><div class="t1">Skyrim Mod Engine</div><div class="t2">Mod Loader &amp; Manager</div></div></div>
  <div class="tag" data-i="tagline"></div>
  <div class="top-right"><span id="ver">v?</span><span>·</span><span>Steam Deck / Linux</span>
   <select id="langSel" title="Language"><option value="uk">Українська</option><option value="en">English</option></select>
   <button class="dots" id="quitBtn" title="">✕</button></div>
 </div>
 <nav class="side" id="side"></nav>
 <div class="main" id="main"></div>
 <div class="log"><div class="lt"><div class="ico">&gt;_</div><div data-i="log"></div></div><pre id="log"></pre>
  <div class="rt"><span class="dot" id="dot"></span><span id="jobtxt"></span><button class="logtoggle" id="logTog" title="">▾</button></div></div>
</div>
<script>
const T="__TOKEN__";
const I18N={uk:{tagline:"Твій світ. Твої моди. Твоя історія.",log:"Журнал подій",home:"Головна",mods:"Моди",fomod:"Пакети (FOMOD)",plugins:"Порядок плагінів",deps:"Залежності",conflicts:"Конфлікти",deploy:"Розгортання",backups:"Резервні копії",settings:"Налаштування",
about:"Більше, ніж просто мод-менеджер. Твій інструмент для створення світу.",ready:"Готово до гри",notdeployed:"Не розгорнуто",other:"У грі інший профіль",pending:"Є нерозгорнуті зміни",working:"Йде робота…",
profile:"Поточний профіль",enabledMods:"Модів увімкнено",totalSize:"Загальний розмір",lastDeploy:"Останнє розгортання",play:"Запустити гру",deployPlay:"Розгорнути й запустити",playOnly:"Лише запустити",dry:"Пробний прогін (без змін)",
addMods:"Додати моди",extract:"Розпакувати нові",installFomod:"Встановити пакети (FOMOD)",search:"Пошук модів…",allCats:"Усі категорії",onlyOff:"Лише вимкнені",onlyOn:"Лише увімкнені",
colName:"Назва моду",colCat:"Категорія",colVer:"Версія",colStatus:"Статус",active:"Активний",off:"Вимкнено",files:"файлів",pl:"плагін(и)",
up:"Вище",down:"Нижче",top:"На початок (низький пріоритет)",bottom:"В кінець (найвищий пріоритет)",openFolder:"Відкрити папку",
cat_skse:"SKSE",cat_ui:"Інтерфейс",cat_gfx:"Графіка",cat_audio:"Звук",cat_anim:"Анімації",cat_plugin:"Плагіни",cat_root:"Папка гри",cat_scripts:"Скрипти",cat_config:"Конфіги",cat_other:"Інше",
category:"Категорія",size:"Розмір",filesCount:"Файлів",version:"Версія",archive:"Архів",nexus:"Сторінка на NexusMods",dependsOn:"Залежить від",neededBy:"Від нього залежать",pluginsL:"Плагіни",sksePl:"SKSE-плагіни",bsa:"BSA-архіви",
fomodChoice:"Вибір FOMOD",wins:"Перекриває",loses:"Перекритий модами",depAnalysis:"Аналіз залежностей",allDepsOk:"Усі залежності задоволені",noSel:"Вибери мод у списку",order:"Позиція в порядку",
pendingBanner:"Порядок чи набір модів змінено — зміни потраплять у гру після розгортання.",deployNow:"Розгорнути",
attention:"Потребують уваги (не розгортаються)",fomodPage:"FOMOD-пакети профілю",fomodHint:"Інтерактивний вибір варіантів поки що робиться в терміналі: python3 mod_engine.py --fomod-redo",
installedTo:"Встановлено в",notInstalled:"Не встановлено",reinstallAuto:"Перевстановити всі (авто)",modeL:"Режим",
pluginsPage:"Порядок плагінів останнього розгортання",noDeploy:"Ще нічого не розгорнуто.",lootUpdate:"Оновити masterlist LOOT і перевірити",
depsPage:"Залежності між модами (за master-файлами плагінів)",problems:"Проблеми порядку",noProblems:"Проблем немає",
conflictsPage:"Конфлікти файлів",recompute:"Перерахувати",notComputed:"Конфлікти ще не рахувалися.",winner:"Перемагає",loser:"Перекритий",count:"Файлів",dupPlugins:"Один плагін у кількох модах",identical:"Однакових файлів (не конфлікт)",
deployPage:"Розгортання",deployBtn:"Розгорнути профіль",undeployBtn:"Повний відкат",skseBtn:"Перевірити SKSE",deployedProfile:"Розгорнутий профіль",filesDeployed:"Файлів у грі",incomplete:"Незавершене! Зроби відкат або розгорни знову.",
skseTitle:"SKSE",gameVer:"Версія гри",sksePlugins:"SKSE-плагіни (статично)",
backupsPage:"Резервні копії та файли профілю",backupFiles:"Оригінальних файлів гри в бекапі",changedSets:"Відкладені змінені файли",overwrite:"Overwrite профілю (створене/змінене грою)",openOw:"Відкрити overwrite",
settingsPage:"Налаштування",profiles:"Профілі",newProfile:"Новий профіль (порожній — моди вмикаєш сам)",npCollSrc:"collection.json або посилання на колекцію Nexus",npFromColl:"Створити з колекції",
npHint:"Новий профіль бере лише порядок модів; усі моди вимкнені. З колекцією — увімкнені рівно моди колекції.",npNeed:"Вкажи назву профілю і файл або посилання колекції.",npFile:"З файлу collection.json…",npLink:"З посилання Nexus…",create:"Створити",del:"Видалити",switchTo:"Перемкнути",paths:"Папки",language:"Мова",quit:"Закрити інтерфейс",
busy:"Зачекай — триває інша операція.",confirmUndeploy:"Прибрати всі моди з гри й відновити оригінали?",confirmDel:"Видалити профіль «%s»?",profileName:"Назва профілю",idle:"Готово",
launchFail:"Не знайшов Steam для запуску. Запусти гру зі Steam.",gameRunning:"Гра вже запущена.",openArchives:"Відкрити папку архівів",noMods:"Модів ще немає. Поклади архіви в mods-archives і натисни «Розпакувати нові».",closed:"Інтерфейс закрито. Цю вкладку можна закрити.",
nexus:"Nexus Mods",nxLogged:"Увійшов як",nxPremium:"Premium",nxFree:"безкоштовний акаунт",nxNone:"Вхід не виконано.",nxKey:"Персональний API-ключ",nxKeyHelp:"nexusmods.com → аватар → Site preferences → API Keys → Personal API key. Ключ зберігається лише на цьому комп'ютері.",
nxLogin:"Увійти",nxLogout:"Вийти",nxHandler:"Приймати «Mod Manager Download» (nxm://)",nxHandlerHelp:"Без Premium Nexus дозволяє завантаження лише кнопкою «Mod Manager Download» на сайті: файл ляже в mods-archives.",
nxColl:"Завантажити відсутні архіви колекції",nxCollAll:"…разом з необов'язковими",nxDownload:"Завантажити з Nexus (ID або посилання)",nxAsk:"ID мода, ID:ID_файлу або посилання на сторінку мода",nxLoginFail:"Ключ не прийнято (див. журнал).",
collection:"Колекція",collNone:"Колекцію не вибрано.",collFetch:"Взяти колекцію з Nexus",collLink:"Посилання на сторінку колекції або її код",collBy:"автор",
collFound:"Архівів є",collMissing:"Бракує",collReq:"обов'язкових",collOpt:"необов'язкових",collDl:"Mod Manager Download",collScan:"Перевірити архіви (розпакувати нові)",
collFreeHint:"Без Premium: натискай «Mod Manager Download» біля кожного мода — файл ляже в mods-archives (потрібен обробник nxm:// у Налаштуваннях). Потім «Перевірити архіви».",
collNeedLogin:"Щоб брати колекції та файли з Nexus, увійди в Налаштуваннях.",optional:"необов'язковий",skseInstall:"Завантажити SKSE з Nexus",
gameBackups:"Бекапи чистої гри",gbHint:"Знімок папки гри разом з INI і plugins.txt — щоб повернутися після поломки чи небажаного оновлення Steam. Робиться, коли моди не розгорнуто.",
gbFull:"Зберегти (повні копії)",gbLink:"Зберегти без місця (посилання)",gbSaves:"Повні копії + сейви",gbName:"Назва (необов'язково)",gbRestore:"Відновити",gbVerify:"Перевірити",gbNone:"Бекапів ще немає.",
gcTitle:"Перевірка і ремонт гри",gcHint:"Порівнює гру зі списком її файлів із SHA-1 (знімок першого запуску): що змінено, чого бракує, що зайве. Ремонт прибирає зайве і повертає файли з бекапів.",
gcCheck:"Перевірити гру",gcRepair:"Відремонтувати",gcBaseline:"Оновити список файлів",gcNoBase:"Списку файлів гри ще немає.",gcBase:"Список файлів",
gbBanner:"Бекапу чистої гри ще немає. Його варто зробити, поки моди не розгорнуто.",gbBannerBtn:"До бекапів",gcRepairConfirm:"Прибрати з гри зайві файли (буде відкладено, не видалено) і повернути змінені з бекапів?",
protect:"Захист гри",prList:"список файлів",prInc:"інкрем. бекап",prOff:"вимкнено",prBackups:"бекапів",
incTitle:"Інкрементальний бекап при розгортанні",incHint:"Перед тим як мод перекриє оригінальний файл гри, його копія зберігається назавжди (кожен вміст один раз). Місце займає лише те, що моди справді замінюють; --game-repair повертає ці файли.",
incOn:"Увімкнути",incOff:"Вимкнути",incState:"Зараз",incStore:"У сховищі",
psTitle:"Захист гри — налаштуй один раз",psHint:"Копіювання файлів гри — лише за твоїм рішенням. Можна змінити будь-коли на сторінці «Резервні копії».",
psList:"Список файлів гри з SHA-1 — щоб знайти змінене й прибрати зайве навіть зі зламаної гри (рекомендовано, місця майже не займає)",
psInc:"Інкрементальний бекап — копія кожного оригінального файлу гри, який перекриває мод (місце — лише ці файли)",
psFull:"Повний бекап зараз — повні копії (стільки ж місця, скільки гра)",psLink:"Повний бекап зараз — посилання (без місця, не захищає від змін на місці)",psNone:"Без повного бекапу",
pArchives:"Папка з архівами модів",pMods:"Папка з розпакованими модами",pSave:"Зберегти",pSaveMove:"Зберегти й перенести вміст",pDefault:"За замовчуванням (поруч зі скриптом)",
pHint:"Архіви можуть лежати де завгодно. Розпаковані моди краще тримати на тому ж диску, що й гру: інакше замість жорстких посилань будуть копії.",pMoveConfirm:"Перенести всі файли зі старої папки в нову?",
psListDone:"Список файлів гри з SHA-1 збережено автоматично (місця не займає).",psApply:"Застосувати",psLater:"Пізніше",psDeployed:"Повний бекап можливий, коли моди не розгорнуто.",
gbConfirm:"Повернути гру до стану «%s»? Моди буде прибрано, замінене відкладеться (не видалиться).",gbDelConfirm:"Видалити бекап «%s»?",gbLinkNote:"link: не займає місця, але не захищає від змін файлів на місці (оновлення Steam)."},
en:{tagline:"Your world. Your mods. Your story.",log:"Event log",home:"Home",mods:"Mods",fomod:"Packages (FOMOD)",plugins:"Plugin order",deps:"Dependencies",conflicts:"Conflicts",deploy:"Deployment",backups:"Backups",settings:"Settings",
about:"More than a mod manager. Your tool for building a world.",ready:"Ready to play",notdeployed:"Not deployed",other:"Another profile is in the game",pending:"Undeployed changes",working:"Working…",
profile:"Current profile",enabledMods:"Mods enabled",totalSize:"Total size",lastDeploy:"Last deployment",play:"Play",deployPlay:"Deploy and play",playOnly:"Just play",dry:"Dry run (no changes)",
addMods:"Add mods",extract:"Extract new",installFomod:"Install packages (FOMOD)",search:"Search mods…",allCats:"All categories",onlyOff:"Disabled only",onlyOn:"Enabled only",
colName:"Mod name",colCat:"Category",colVer:"Version",colStatus:"Status",active:"Active",off:"Disabled",files:"files",pl:"plugin(s)",
up:"Up",down:"Down",top:"To top (lowest priority)",bottom:"To bottom (highest priority)",openFolder:"Open folder",
cat_skse:"SKSE",cat_ui:"Interface",cat_gfx:"Graphics",cat_audio:"Audio",cat_anim:"Animations",cat_plugin:"Plugins",cat_root:"Game folder",cat_scripts:"Scripts",cat_config:"Configs",cat_other:"Other",
category:"Category",size:"Size",filesCount:"Files",version:"Version",archive:"Archive",nexus:"NexusMods page",dependsOn:"Depends on",neededBy:"Needed by",pluginsL:"Plugins",sksePl:"SKSE plugins",bsa:"BSA archives",
fomodChoice:"FOMOD choice",wins:"Overrides",loses:"Overridden by",depAnalysis:"Dependency analysis",allDepsOk:"All dependencies satisfied",noSel:"Select a mod in the list",order:"Position in order",
pendingBanner:"Mod order or selection changed — the game gets the changes after deployment.",deployNow:"Deploy",
attention:"Need attention (not deployed)",fomodPage:"FOMOD packages of this profile",fomodHint:"Interactive option choice is in the terminal for now: python3 mod_engine.py --fomod-redo",
installedTo:"Installed to",notInstalled:"Not installed",reinstallAuto:"Reinstall all (auto)",modeL:"Mode",
pluginsPage:"Plugin order of the last deployment",noDeploy:"Nothing deployed yet.",lootUpdate:"Update LOOT masterlist and check",
depsPage:"Dependencies between mods (by plugin masters)",problems:"Order problems",noProblems:"No problems",
conflictsPage:"File conflicts",recompute:"Recompute",notComputed:"Conflicts not computed yet.",winner:"Wins",loser:"Overridden",count:"Files",dupPlugins:"Same plugin in several mods",identical:"Identical files (not a conflict)",
deployPage:"Deployment",deployBtn:"Deploy profile",undeployBtn:"Full rollback",skseBtn:"Check SKSE",deployedProfile:"Deployed profile",filesDeployed:"Files in the game",incomplete:"Incomplete! Roll back or deploy again.",
skseTitle:"SKSE",gameVer:"Game version",sksePlugins:"SKSE plugins (static)",
backupsPage:"Backups and profile files",backupFiles:"Original game files in backup",changedSets:"Changed files set aside",overwrite:"Profile overwrite (created/changed by the game)",openOw:"Open overwrite",
settingsPage:"Settings",profiles:"Profiles",newProfile:"New profile (empty — you enable the mods)",npCollSrc:"collection.json or a Nexus collection link",npFromColl:"Create from collection",
npHint:"A new profile takes only the mod order; all mods are disabled. With a collection — exactly the collection's mods are enabled.",npNeed:"Enter the profile name and the collection file or link.",npFile:"From collection.json file…",npLink:"From Nexus link…",create:"Create",del:"Delete",switchTo:"Switch",paths:"Folders",language:"Language",quit:"Close interface",
busy:"Please wait — another operation is running.",confirmUndeploy:"Remove all mods from the game and restore originals?",confirmDel:"Delete profile «%s»?",profileName:"Profile name",idle:"Ready",
launchFail:"Steam not found to launch the game. Start it from Steam.",gameRunning:"The game is already running.",openArchives:"Open archives folder",noMods:"No mods yet. Put archives into mods-archives and press “Extract new”.",closed:"Interface closed. You can close this tab.",
nexus:"Nexus Mods",nxLogged:"Logged in as",nxPremium:"Premium",nxFree:"free account",nxNone:"Not logged in.",nxKey:"Personal API key",nxKeyHelp:"nexusmods.com → avatar → Site preferences → API Keys → Personal API key. The key is stored only on this computer.",
nxLogin:"Log in",nxLogout:"Log out",nxHandler:"Accept “Mod Manager Download” (nxm://)",nxHandlerHelp:"Without Premium, Nexus allows downloads only through the “Mod Manager Download” button on the site: the file lands in mods-archives.",
nxColl:"Download missing collection archives",nxCollAll:"…including optional ones",nxDownload:"Download from Nexus (ID or link)",nxAsk:"Mod ID, ID:file ID or the mod page link",nxLoginFail:"Key not accepted (see the log).",
collection:"Collection",collNone:"No collection selected.",collFetch:"Get a collection from Nexus",collLink:"Collection page link or its code",collBy:"by",
collFound:"Archives present",collMissing:"Missing",collReq:"required",collOpt:"optional",collDl:"Mod Manager Download",collScan:"Check archives (extract new)",
collFreeHint:"Without Premium: press “Mod Manager Download” next to each mod — the file lands in mods-archives (needs the nxm:// handler in Settings). Then “Check archives”.",
collNeedLogin:"To get collections and files from Nexus, log in under Settings.",optional:"optional",skseInstall:"Download SKSE from Nexus",
gameBackups:"Clean game backups",gbHint:"A snapshot of the game folder with INI files and plugins.txt — to get back after a breakage or an unwanted Steam update. Made while no mods are deployed.",
gbFull:"Save (full copies)",gbLink:"Save without space (links)",gbSaves:"Full copies + saves",gbName:"Name (optional)",gbRestore:"Restore",gbVerify:"Verify",gbNone:"No backups yet.",
gcTitle:"Game check and repair",gcHint:"Compares the game with the list of its files and SHA-1 (taken on the first run): what changed, what is missing, what is extra. Repair removes extras and restores files from backups.",
gcCheck:"Check the game",gcRepair:"Repair",gcBaseline:"Refresh the file list",gcNoBase:"No file list of the game yet.",gcBase:"File list",
gbBanner:"No clean game backup yet. It is best made while no mods are deployed.",gbBannerBtn:"To backups",gcRepairConfirm:"Remove extra files from the game (set aside, not deleted) and restore changed ones from backups?",
protect:"Game protection",prList:"file list",prInc:"incremental backup",prOff:"off",prBackups:"backups",
incTitle:"Incremental backup on deployment",incHint:"Before a mod overrides an original game file, a copy of it is kept permanently (each content once). It takes only the space of what mods actually replace; --game-repair returns these files.",
incOn:"Enable",incOff:"Disable",incState:"Now",incStore:"In storage",
psTitle:"Game protection — set it up once",psHint:"Copying game files happens only if you decide so. Change it any time on the “Backups” page.",
psList:"File list of the game with SHA-1 — to find changes and remove extras even from a broken game (recommended, almost no space)",
psInc:"Incremental backup — a copy of every original game file a mod overrides (space: only those files)",
psFull:"Full backup now — full copies (as much space as the game)",psLink:"Full backup now — links (no space, no protection against in-place changes)",psNone:"No full backup",
pArchives:"Mod archives folder",pMods:"Extracted mods folder",pSave:"Save",pSaveMove:"Save and move contents",pDefault:"Default (next to the script)",
pHint:"Archives can be anywhere. Extracted mods are best kept on the same drive as the game: otherwise deployment copies instead of hard-linking.",pMoveConfirm:"Move all files from the old folder to the new one?",
psListDone:"The list of game files with SHA-1 was saved automatically (no space).",psApply:"Apply",psLater:"Later",psDeployed:"A full backup is possible while no mods are deployed.",
gbConfirm:"Return the game to «%s»? Mods are removed first; replaced files are set aside (not deleted).",gbDelConfirm:"Delete backup «%s»?",gbLinkNote:"link: uses no space but does not protect against files changed in place (Steam updates)."}};
let S=null,L="uk",page="home",sel=null,q="",cat="",logFrom=0,running=false,dragName=null;
const $=s=>document.querySelector(s);
const t=k=>(I18N[L]||I18N.uk)[k]||k;
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
async function api(path,body){const o={headers:{"X-Token":T}};if(body!==undefined){o.method="POST";o.headers["Content-Type"]="application/json";o.body=JSON.stringify(body)}
 let r;try{r=await fetch(path,o)}catch(e){showErr("Нема зв'язку з сервером інтерфейсу / no connection to the interface server: "+e);throw e}
 const j=await r.json().catch(()=>({}));if(r.status===500&&j.detail)showErr("ERROR "+path+"\n"+j.detail+"\n(mod_engine_output/gui_error.log)");return j}
function showErr(m){const pre=document.querySelector("#log");if(pre){pre.textContent+=(pre.textContent?"\n":"")+m;pre.scrollTop=pre.scrollHeight}}
const ICONS={home:'<path d="M3 11l9-7 9 7v9h-6v-6H9v6H3z"/>',mods:'<path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"/>',fomod:'<path d="M12 2l9 5v10l-9 5-9-5V7zM12 12l9-5M12 12v10M12 12L3 7"/>',
plugins:'<path d="M4 6h16M4 12h16M4 18h16"/>',collection:'<path d="M4 4h16v4H4zM6 8v12h12V8M10 12h4"/>',deps:'<path d="M12 3v6M6 21v-6l6-6 6 6v6"/>',conflicts:'<path d="M12 3l10 18H2zM12 10v5M12 18v1"/>',deploy:'<path d="M12 3l8 4-8 4-8-4zM4 12l8 4 8-4M4 17l8 4 8-4"/>',
backups:'<path d="M12 7v5l3 2M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5"/>',settings:'<path d="M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.2V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-2.7-1.1l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 3.4 14H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.2 7.3l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.6 1.6 0 0 0 9.7 3.4V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0 1.1 2.7H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>'};
function icon(k){return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round">${ICONS[k]}</svg>`}
function avatar(n){let h=0;for(const c of n)h=(h*31+c.charCodeAt(0))>>>0;const hue=h%360;const w=n.replace(/^[^\p{L}\p{N}]+/u,"").split(/\s+/).filter(Boolean);
 const ini=((w[0]||"?")[0]+((w[1]||"")[0]||"")).toUpperCase();return `<div class="av" style="background:linear-gradient(135deg,hsl(${hue} 45% 38%),hsl(${(hue+40)%360} 50% 24%))">${esc(ini)}</div>`}
function fmtSize(b){if(!b)return"0 B";const u=["B","KiB","MiB","GiB","TiB"];let i=0;while(b>=1024&&i<4){b/=1024;i++}return (i?b.toFixed(1):b)+" "+u[i]}
function fmtDate(s){if(!s)return"—";const d=new Date(s);return isNaN(d)?s:d.toLocaleString(L==="uk"?"uk-UA":"en-GB",{dateStyle:"short",timeStyle:"short"})}
function nav(){const c=S.conflicts.pairs_total,pr=S.problems.length;
 const items=[["home",""],["mods",S.mods.length],["fomod",S.fomods.length],["collection",S.collection?S.collection.mods.filter(m=>!m.have&&!m.optional).length||"":""],["plugins",S.deployed?S.deployed.plugins.length:""],["deps",pr?pr:""],["conflicts",c||""],["deploy",""],["backups",(!S.protection.asked||!S.baseline)?"!":""],["settings",""]];
 $("#side").innerHTML=items.map(([k,n])=>`<button class="nav ${page===k?"on":""}" data-p="${k}">${icon(k)}<span>${t(k)}</span>${n!==""?`<span class="cnt ${(k==="deps"&&pr)||(k==="conflicts"&&c)||n==="!"?"bad":""}">${n}</span>`:""}</button>`).join("")+
 `<div class="about"><b>Skyrim Mod Engine</b>v${esc(S.version)}<hr style="border:0;border-top:1px solid var(--line)">${t("about")}</div>`;
 document.querySelectorAll(".nav").forEach(b=>b.onclick=()=>{page=b.dataset.p;render()})}
function statusCard(){const d=S.deployed,on=S.mods.filter(m=>m.enabled);let title=t("ready"),cls="okline";
 if(running){title=t("working");cls="warnline"}else if(!d){title=t("notdeployed");cls="warnline"}else if(d.profile!==S.profile){title=t("other")+": "+d.profile;cls="warnline"}else if(!d.complete){title=t("incomplete");cls="badline"}else if(S.pending){title=t("pending");cls="warnline"}
 return `<div class="card status"><h3 class="${cls}">● ${esc(title)}</h3><div class="kv">
 <span class="k">${t("profile")}:</span><span><select id="profSel">${S.profiles.map(p=>`<option ${p===S.profile?"selected":""}>${esc(p)}</option>`).join("")}</select></span>
 <span class="k">${t("enabledMods")}:</span><span>${on.length} / ${S.mods.length}</span>
 <span class="k">${t("totalSize")}:</span><span>${fmtSize(on.reduce((a,m)=>a+(m.size_bytes||0),0))}</span>
 <span class="k">${t("lastDeploy")}:</span><span>${d?fmtDate(d.at)+(d.profile!==S.profile?" ("+esc(d.profile)+")":""):"—"}</span>
 <span class="k">${t("protect")}:</span><span><a class="link" onclick="page='backups';render()">${protLine()}</a></span></div>
 <div class="launch"><button class="btn go" id="playBtn" ${running?"disabled":""}>▶ ${t((!d||d.profile!==S.profile||S.pending||!d.complete)?"deployPlay":"play")}</button><button class="btn more" id="playMore">▾</button></div></div>`}
function protLine(){const pr=S.protection||{},ok=s=>`<span class="okline">✔</span> ${s}`,no=s=>`<span class="warnline">✖</span> ${s}`;
 return [S.baseline?ok(t("prList")):no(t("prList")),pr.incremental?ok(t("prInc")+(pr.store_files?` (${pr.store_files}, ${fmtSize(pr.store_size)})`:"")):no(t("prInc")+": "+t("prOff")),
  ((S.game_backups||[]).length?ok:no)((S.game_backups||[]).length+" "+t("prBackups"))].join("<br>")}
function bindStatus(){const ps=$("#profSel");if(ps)ps.onchange=()=>run("profile",ps.value);
 const pb=$("#playBtn");if(pb)pb.onclick=()=>{const d=S.deployed;(!d||d.profile!==S.profile||S.pending||!d.complete)?run("deploy_launch"):launch()};
 const pm=$("#playMore");if(pm)pm.onclick=ev=>menu(ev,[[t("deployPlay"),()=>run("deploy_launch")],[t("playOnly"),launch],[t("dry"),()=>run("dry")]])}
function filtered(){return S.mods.filter(m=>(!q||m.name.toLowerCase().includes(q.toLowerCase()))&&(!cat||(cat==="__on"?m.enabled:cat==="__off"?!m.enabled:m.category===cat)))}
function protSetup(){const dep=!!S.deployed;return `<div class="card" style="padding:14px;border-color:#2f7fd4"><h3 style="margin:0 0 4px">🛡 ${t("psTitle")}</h3><p class="m" style="margin:0 0 8px">${t("psHint")}</p>
 ${S.baseline?`<div class="okline" style="margin:4px 0">✔ ${t("psListDone")}</div>`:""}
 <label style="display:block;margin:6px 0"><input type="checkbox" id="psInc"> ${t("psInc")}</label>
 ${dep?`<p class="m" style="margin:6px 0">${t("psDeployed")}</p>`:`<label style="display:block;margin:4px 0"><input type="radio" name="psB" value="" checked> ${t("psNone")}</label>
 <label style="display:block;margin:4px 0"><input type="radio" name="psB" value="full"> ${t("psFull")}</label><label style="display:block;margin:4px 0"><input type="radio" name="psB" value="link"> ${t("psLink")}</label>`}
 <div class="toolbar" style="margin-top:10px"><button class="btn pri" onclick="protApply()">${t("psApply")}</button></div></div>`}
let protLater=false;
async function protApply(){const b=document.querySelector('input[name=psB]:checked');const r=await api("/api/protection",{baseline:false,incremental:$("#psInc").checked,backup:b?b.value:""});
 if(r.error){alert(r.error==="busy"?t("busy"):r.error);return}if(r.job){running=true;jobLine()}await refresh()}
function modsView(){const cats=[...new Set(S.mods.map(m=>m.category))];const list=filtered();
 const gbBan=(!S.protection.asked&&!protLater)?`<div class="banner">🛡 ${t("psTitle")} <button class="btn pri" style="margin-left:auto" onclick="page='backups';render()">${t("gbBannerBtn")}</button><button class="btn" onclick="protLater=true;renderMain()">${t("psLater")}</button></div>`:"";
 const ban=S.pending&&S.deployed&&S.deployed.profile===S.profile?`<div class="banner">⚠ ${t("pendingBanner")} <button class="btn pri" style="margin-left:auto" onclick="run('deploy')">${t("deployNow")}</button></div>`:"";
 return `${gbBan}${ban}<div class="toolbar"><button class="btn pri" id="addBtn">＋ ${t("addMods")}</button><button class="btn" onclick="run('scan')">⤓ ${t("extract")}</button><button class="btn" onclick="run('fomod_auto')">⬢ ${t("installFomod")}</button></div>
 <div class="toolbar"><div class="search">⌕<input id="q" placeholder="${t("search")}" value="${esc(q)}"></div>
 <select id="cat"><option value="">${t("allCats")}</option><option value="__on" ${cat==="__on"?"selected":""}>${t("onlyOn")}</option><option value="__off" ${cat==="__off"?"selected":""}>${t("onlyOff")}</option>${cats.map(c=>`<option value="${c}" ${cat===c?"selected":""}>${t("cat_"+c)}</option>`).join("")}</select></div>
 <div class="card tablewrap">${S.mods.length?`<table><thead><tr><th class="c-num">#</th><th>${t("colName")}</th><th class="c-cat">${t("colCat")}</th><th class="c-ver">${t("colVer")}</th><th>${t("colStatus")}</th><th></th></tr></thead><tbody>
 ${list.map(m=>`<tr class="row ${sel===m.name?"sel":""}" draggable="true" data-n="${esc(m.name)}"><td style="color:var(--mut)">${m.index+1}</td>
 <td><div class="mname">${avatar(m.name)}<div><div class="n">${esc(m.name)}</div><div class="s">${m.files} ${t("files")}${m.plugins.length?" · "+m.plugins.length+" "+t("pl"):""} · ${esc(m.size||"")}</div></div></div></td>
 <td class="c-cat"><span class="chip c-${m.category}">${t("cat_"+m.category)}</span></td><td class="c-ver">${esc(m.version||"—")}</td>
 <td><div class="st"><button class="sw ${m.enabled?"on":""}" data-sw="${esc(m.name)}"></button><span class="${m.enabled?"on":"off"}">${t(m.enabled?"active":"off")}</span></div></td>
 <td><button class="dots" data-menu="${esc(m.name)}">⋮</button></td></tr>`).join("")}</tbody></table>`:`<div class="empty">${t("noMods")}</div>`}</div>`}
function bindMods(){const qi=$("#q");if(qi)qi.oninput=()=>{q=qi.value;const p=qi.selectionStart;renderMain();const n=$("#q");n.focus();n.setSelectionRange(p,p)};
 const c=$("#cat");if(c)c.onchange=()=>{cat=c.value;renderMain()};const ab=$("#addBtn");if(ab)ab.onclick=ev=>menu(ev,[[t("openArchives"),()=>api("/api/open",{what:"archives"})],[t("extract"),()=>run("scan")]].concat(S.nexus&&S.nexus.premium?[[t("nxDownload"),nxDl]]:[]).concat(S.nexus&&S.nexus.premium&&S.collection?[[t("nxColl"),()=>run("collection_download")]]:[]));
 document.querySelectorAll("tr.row").forEach(r=>{r.onclick=ev=>{if(ev.target.closest(".sw,.dots"))return;sel=r.dataset.n;renderMain()};
  r.ondragstart=()=>{dragName=r.dataset.n};r.ondragover=ev=>{ev.preventDefault();r.classList.add("drag-over")};r.ondragleave=()=>r.classList.remove("drag-over");
  r.ondrop=async ev=>{ev.preventDefault();r.classList.remove("drag-over");if(!dragName||dragName===r.dataset.n)return;const to=S.mods.find(m=>m.name===r.dataset.n).index;await move(dragName,to)}});
 document.querySelectorAll("[data-sw]").forEach(b=>b.onclick=async()=>{const m=S.mods.find(x=>x.name===b.dataset.sw);await post("/api/toggle",{name:m.name,enabled:!m.enabled})});
 const os=$("#openSel");if(os)os.onclick=()=>api("/api/open",{what:"mod:"+sel});
 document.querySelectorAll("[data-menu]").forEach(b=>b.onclick=ev=>{const m=S.mods.find(x=>x.name===b.dataset.menu);
  menu(ev,[[t("up"),()=>move(m.name,m.index-1)],[t("down"),()=>move(m.name,m.index+1)],[t("top"),()=>move(m.name,0)],[t("bottom"),()=>move(m.name,S.mods.length)],[t("openFolder"),()=>api("/api/open",{what:"mod:"+m.name})]])})}
async function post(p,b){const r=await api(p,b);if(r.error==="busy")alert(t("busy"));await refresh()}
async function move(n,to){await post("/api/move",{name:n,to:Math.max(0,to)})}
function detail(){const m=S.mods.find(x=>x.name===sel);if(!m)return `<div class="card detail"><div class="empty">${t("noSel")}</div></div>`;
 const li=a=>a.length?`<ul>${a.map(x=>`<li>${esc(x)}</li>`).join("")}</ul>`:"—";
 const steps=m.fomod_steps?m.fomod_steps.map(s=>s.groups.map(g=>`<li><span style="color:var(--mut)">${esc(String(g.group).replace(/:\s*$/,""))}:</span> ${esc((g.selected||[]).join(", ")||"—")}</li>`).join("")).join(""):"";
 return `<div class="card detail"><div class="dhead">${avatar(m.name)}<div><div class="n">${esc(m.name)}</div><div class="v">${m.version?"v"+esc(m.version):""}</div></div></div>
 <div class="dsec"><div class="kv"><span class="k">${t("category")}:</span><span>${t("cat_"+m.category)}</span><span class="k">${t("size")}:</span><span>${esc(m.size)}</span>
 <span class="k">${t("filesCount")}:</span><span>${m.files}</span><span class="k">${t("order")}:</span><span>${m.index+1}</span>${m.archive?`<span class="k">${t("archive")}:</span><span style="word-break:break-all">${esc(m.archive)}</span>`:""}</div>
 ${m.nexus_id?`<div style="margin-top:8px"><a class="link" href="https://www.nexusmods.com/skyrimspecialedition/mods/${m.nexus_id}" target="_blank" rel="noopener">↗ ${t("nexus")}</a></div>`:""}
 <div style="margin-top:6px"><a class="link" id="openSel">📂 ${t("openFolder")}</a></div></div>
 ${m.plugins.length?`<div class="dsec"><h4>${t("pluginsL")}</h4>${li(m.plugins)}</div>`:""}${m.skse_plugins.length?`<div class="dsec"><h4>${t("sksePl")}</h4>${li(m.skse_plugins)}</div>`:""}
 ${m.bsa.length?`<div class="dsec"><h4>${t("bsa")}</h4>${li(m.bsa)}</div>`:""}${steps?`<div class="dsec"><h4>${t("fomodChoice")}</h4><ul>${steps}</ul></div>`:""}
 <div class="dsec"><h4>${t("depAnalysis")}</h4>${m.deps.length?`<div>${t("dependsOn")}:</div>${li(m.deps)}`:""}${m.rdeps.length?`<div>${t("neededBy")}:</div>${li(m.rdeps)}`:""}
 ${S.problems.some(p=>p.includes("«"+m.name+"»"))?S.problems.filter(p=>p.includes("«"+m.name+"»")).map(p=>`<div class="badline">⚠ ${esc(p)}</div>`).join(""):`<div class="okline">✔ ${t("allDepsOk")}</div>`}</div>
 ${m.wins.length||m.loses.length?`<div class="dsec">${m.wins.length?`<h4>${t("wins")}</h4>${li(m.wins.map(([n,c])=>n+" ("+c+")"))}`:""}${m.loses.length?`<h4>${t("loses")}</h4>${li(m.loses.map(([n,c])=>n+" ("+c+")"))}`:""}</div>`:""}</div>`}
function pageWrap(h){return `<div class="card page">${h}</div>`}
function fomodView(){return pageWrap(`<h2>${t("fomodPage")}</h2><p class="m">${t("fomodHint")}</p><div class="toolbar" style="margin-bottom:12px"><button class="btn pri" onclick="run('fomod_auto')">${t("installFomod")}</button><button class="btn" onclick="run('fomod_redo_auto')">${t("reinstallAuto")}</button></div>
 <div class="grid2">${S.fomods.map(f=>`<div class="box"><b>${esc(f.name)}</b><div style="color:var(--mut);font-size:12.5px;margin:4px 0">${f.installed_to?t("installedTo")+": "+esc(f.installed_to):`<span class="warnline">${t("notInstalled")}</span>`}${f.mode?" · "+t("modeL")+": "+esc(f.mode):""}</div>
 ${f.steps?`<ul style="margin:0;padding-left:18px;font-size:12.5px">${f.steps.map(s=>s.groups.map(g=>`<li><span style="color:var(--mut)">${esc(String(g.group).replace(/:\s*$/,""))}:</span> ${esc((g.selected||[]).join(", ")||"—")}</li>`).join("")).join("")}</ul>`:""}</div>`).join("")||`<div class="empty">—</div>`}</div>
 ${S.attention.length?`<h2 style="margin-top:18px">${t("attention")}</h2><div class="grid2">${S.attention.map(a=>`<div class="box"><b>${esc(a.name)}</b><div class="warnline" style="font-size:12.5px">${esc(a.layout)}${a.options.length?" — "+esc(a.options.slice(0,6).join(", ")):""}</div></div>`).join("")}</div>`:""}`)}
function collectionView(){const c=S.collection,pr=S.nexus&&S.nexus.premium;
 const fetchBox=S.nexus?`<div class="toolbar" style="margin:10px 0"><div class="search"><input id="collLink" placeholder="${t("collLink")}"></div><button class="btn pri" onclick="const v=$('#collLink').value.trim();v&&run('collection_nexus',v)">${t("collFetch")}</button></div>`:`<p class="m">${t("collNeedLogin")}</p>`;
 if(!c)return pageWrap(`<h2>${t("collection")}</h2><p class="m">${t("collNone")}</p>${fetchBox}`);
 const req=c.mods.filter(m=>!m.optional),opt=c.mods.filter(m=>m.optional),miss=c.mods.filter(m=>!m.have);
 return pageWrap(`<h2>${esc(c.name||"?")} <span style="color:var(--mut);font-size:14px">${t("collBy")} ${esc(c.author||"?")}</span></h2>
 <p class="m">${t("collFound")}: ${t("collReq")} ${req.filter(m=>m.have).length}/${req.length}, ${t("collOpt")} ${opt.filter(m=>m.have).length}/${opt.length}</p>
 <div class="toolbar"><button class="btn" onclick="run('scan')">${t("collScan")}</button>${pr&&miss.length?`<button class="btn go" onclick="run('collection_download')">${t("nxColl")}</button><button class="btn" onclick="run('collection_download_all')">${t("nxCollAll")}</button>`:""}</div>
 ${fetchBox}${!pr&&miss.length?`<p class="m">${t("collFreeHint")}</p>`:""}
 ${miss.length?`<h3>${t("collMissing")}: ${miss.length}</h3><table><tbody>${miss.map(m=>`<tr><td>${esc(m.name)} <span style="color:var(--mut)">${esc(m.version||"")}</span>${m.optional?` <span class="chip">${t("optional")}</span>`:""}</td>
 <td style="text-align:right">${m.mod_id&&m.file_id?`<a class="btn" style="padding:4px 10px" target="_blank" rel="noopener" href="https://www.nexusmods.com/skyrimspecialedition/mods/${+m.mod_id}?tab=files&file_id=${+m.file_id}&nmm=1">${t("collDl")}</a>`:"—"}</td></tr>`).join("")}</tbody></table>`:""}`)}
function pluginsView(){const d=S.deployed;return pageWrap(`<h2>${t("pluginsPage")}</h2>${d?`<p class="m">${esc(d.profile)} · ${fmtDate(d.at)} · ${d.plugins.length}</p><div class="toolbar" style="margin-bottom:10px"><button class="btn" onclick="run('loot_update')">${t("lootUpdate")}</button></div><ol class="mono">${d.plugins.map(p=>`<li>${esc(p)}</li>`).join("")}</ol>`:`<p class="m">${t("noDeploy")}</p>`}`)}
function depsView(){const withDeps=S.mods.filter(m=>m.deps.length);return pageWrap(`<h2>${t("depsPage")}</h2><h3>${t("problems")}</h3>${S.problems.length?S.problems.map(p=>`<div class="badline">⚠ ${esc(p)}</div>`).join(""):`<div class="okline">✔ ${t("noProblems")}</div>`}
 <div class="grid2" style="margin-top:14px">${withDeps.map(m=>`<div class="box"><b>${esc(m.name)}</b><div style="font-size:12.5px;color:var(--mut)">${t("dependsOn")}:</div><ul style="margin:0;padding-left:18px">${m.deps.map(x=>`<li>${esc(x)}</li>`).join("")}</ul></div>`).join("")}</div>`)}
function conflictsView(){const c=S.conflicts;return pageWrap(`<h2>${t("conflictsPage")}</h2><div class="toolbar" style="margin-bottom:12px"><button class="btn pri" onclick="run('conflicts')">${t("recompute")}</button></div>
 ${!c.computed?`<p class="m">${t("notComputed")}</p>`:`<p class="m">${c.pairs_total} · ${t("identical")}: ${c.identical}</p>
 ${c.duplicate_plugins.length?`<h3 class="badline">${t("dupPlugins")}</h3><ul>${c.duplicate_plugins.map(([p,ms])=>`<li><b>${esc(p)}</b>: ${esc(ms.join(", "))}</li>`).join("")}</ul>`:""}
 <table><thead><tr><th>${t("winner")}</th><th>${t("loser")}</th><th>${t("count")}</th><th></th></tr></thead><tbody>${c.pairs.map(p=>`<tr><td>${esc(p.winner)}</td><td>${esc(p.loser)}</td><td>${p.count}</td><td style="color:var(--mut);font-size:12px">${esc(Object.entries(p.by_severity||{}).map(([k,v])=>k+": "+v).join(", "))}</td></tr>`).join("")}</tbody></table>`}`)}
function deployView(){const d=S.deployed,k=S.skse;const pl=k&&k.plugins?k.plugins:[];const cnt={};pl.forEach(p=>cnt[p.status]=(cnt[p.status]||0)+1);
 return pageWrap(`<h2>${t("deployPage")}</h2><div class="toolbar" style="margin-bottom:14px"><button class="btn go" onclick="run('deploy')">${t("deployBtn")} «${esc(S.profile)}»</button><button class="btn" onclick="run('dry')">${t("dry")}</button>
 <button class="btn" onclick="run('skse')">${t("skseBtn")}</button>${S.nexus?`<button class="btn" onclick="run('skse_install')">${t("skseInstall")}</button>`:""}<button class="btn danger" onclick="confirm(t('confirmUndeploy'))&&run('undeploy')">${t("undeployBtn")}</button></div>
 <div class="grid2"><div class="box"><div class="kv"><span class="k">${t("deployedProfile")}:</span><span>${d?esc(d.profile):"—"}</span><span class="k">${t("lastDeploy")}:</span><span>${d?fmtDate(d.at):"—"}</span>
 <span class="k">${t("filesDeployed")}:</span><span>${d?d.files:"—"}</span></div>${d&&!d.complete?`<div class="badline" style="margin-top:8px">${t("incomplete")}</div>`:""}</div>
 ${k?`<div class="box"><b>${t("skseTitle")}</b><div class="kv" style="margin-top:6px"><span class="k">${t("gameVer")}:</span><span>${esc(k.game_version||"?")}</span><span class="k">SKSE:</span><span class="${k.skse&&k.skse.matches_game?"okline":"warnline"}">${k.skse&&k.skse.runtime_dlls&&k.skse.runtime_dlls.length?esc(k.skse.runtime_dlls.join(", ")):"—"}</span>
 <span class="k">${t("sksePlugins")}:</span><span>${Object.entries(cnt).map(([s,n])=>s+": "+n).join(", ")||"—"}</span></div>${(k.warnings||[]).map(w=>`<div class="warnline" style="font-size:12.5px">⚠ ${esc(w)}</div>`).join("")}</div>`:""}</div>`)}
function backupsView(){const b=S.backups,o=S.overwrite,gb=S.game_backups||[];const nm=()=>($("#gbName")?$("#gbName").value.trim():"");
 const setup=!S.protection.asked?protSetup()+"<div style='height:12px'></div>":"";
 const gbox=`<h2>${t("gameBackups")}</h2><p class="m">${t("gbHint")}</p><div class="toolbar" style="margin-bottom:10px"><div class="search" style="max-width:260px"><input id="gbName" placeholder="${t("gbName")}"></div>
 <button class="btn pri" onclick="run('backup_full',$('#gbName').value.trim())">${t("gbFull")}</button><button class="btn" onclick="run('backup_link',$('#gbName').value.trim())">${t("gbLink")}</button><button class="btn" onclick="run('backup_full_saves',$('#gbName').value.trim())">${t("gbSaves")}</button></div>
 <p class="m" style="font-size:12.5px">${t("gbLinkNote")}</p>
 ${gb.length?`<table><tbody>${gb.map(x=>`<tr><td><b>${esc(x.name)}</b><div style="color:var(--mut);font-size:12.5px">${esc(x.game_version)} · ${x.files} ${t("files")} · ${fmtSize(x.size)} · ${esc(x.mode)}${x.saves?" · saves":""} · ${fmtDate(x.created)}</div></td>
 <td style="text-align:right;white-space:nowrap"><button class="btn" style="padding:4px 10px" data-gb="restore" data-n="${esc(x.name)}">${t("gbRestore")}</button> <button class="btn" style="padding:4px 10px" data-gb="verify" data-n="${esc(x.name)}">${t("gbVerify")}</button> <button class="btn danger" style="padding:4px 10px" data-gb="delete" data-n="${esc(x.name)}">${t("del")}</button></td></tr>`).join("")}</tbody></table>`:`<p class="m">${t("gbNone")}</p>`}
 <h2 style="margin-top:20px">${t("incTitle")}</h2><p class="m">${t("incHint")}</p>
 <div class="toolbar"><span>${t("incState")}: <b class="${S.protection.incremental?"okline":"warnline"}">${S.protection.incremental?"✔":"✖ "+t("prOff")}</b> · ${t("incStore")}: ${S.protection.store_files} · ${fmtSize(S.protection.store_size)}</span>
 ${S.protection.incremental?`<button class="btn" onclick="run('incremental_off')">${t("incOff")}</button>`:`<button class="btn pri" onclick="run('incremental_on')">${t("incOn")}</button>`}</div>
 <h2 style="margin-top:20px">${t("gcTitle")}</h2><p class="m">${t("gcHint")}</p>
 <p class="m">${S.baseline?`${t("gcBase")}: ${esc(S.baseline.version)} · ${S.baseline.files} ${t("files")} · ${fmtDate(S.baseline.created)}`:t("gcNoBase")}</p>
 <div class="toolbar"><button class="btn pri" onclick="run('game_check')">${t("gcCheck")}</button><button class="btn" onclick="confirm(t('gcRepairConfirm'))&&run('game_repair')">${t("gcRepair")}</button><button class="btn" onclick="run('game_baseline')">${t("gcBaseline")}</button></div>
 <h2 style="margin-top:20px">${t("backupsPage")}</h2>`;
 return pageWrap(setup+gbox+`<div class="grid2"><div class="box"><div class="kv"><span class="k">${t("backupFiles")}:</span><span>${b.files}</span><span class="k">${t("changedSets")}:</span><span>${b.changed_sets.length}</span></div>
 ${b.changed_sets.length?`<ul class="mono">${b.changed_sets.map(x=>`<li>deploy_changed/${esc(x)}</li>`).join("")}</ul>`:""}<div style="margin-top:8px"><a class="link" onclick="api('/api/open',{what:'output'})">📂 mod_engine_output</a></div></div>
 <div class="box"><b>${t("overwrite")}: ${o.total}</b><div style="margin:6px 0"><a class="link" onclick="api('/api/open',{what:'overwrite'})">📂 ${t("openOw")}</a></div><ul class="mono" style="max-height:300px;overflow:auto">${o.files.map(f=>`<li>${esc(f)}</li>`).join("")}</ul></div></div>`)}
function settingsView(){const d=S.deployed;return pageWrap(`<h2>${t("settingsPage")}</h2><div class="grid2"><div class="box"><b>${t("profiles")}</b><ul style="padding-left:18px">${S.profiles.map(p=>`<li style="margin:6px 0">${esc(p)} ${p===S.profile?"✔":`<button class="btn" style="padding:3px 8px" onclick='run("profile",${JSON.stringify(p)})'>${t("switchTo")}</button>`}
 ${p!==S.profile&&!(d&&d.profile===p)?`<button class="btn danger" style="padding:3px 8px" onclick='confirm(t("confirmDel").replace("%s",${JSON.stringify(p)}))&&run("profile_delete",${JSON.stringify(p)})'>${t("del")}</button>`:""}</li>`).join("")}</ul>
 <div style="margin-top:8px">${t("newProfile")}:</div><div class="toolbar" style="margin-top:6px"><div class="search"><input id="newProf" placeholder="${t("profileName")}"></div><button class="btn pri" onclick="const v=$('#newProf').value.trim();v&&run('profile_new',v)">${t("create")}</button></div>
 <div class="toolbar" style="margin-top:6px"><div class="search"><input id="newProfColl" placeholder="${t("npCollSrc")}"></div><button class="btn" onclick="newProfColl()">${t("npFromColl")}</button></div>
 <div style="font-size:12px;color:var(--mut);margin-top:4px">${t("npHint")}</div></div>
 <div class="box"><b>${t("paths")}</b>
 ${["archives","mods"].map(k=>`<div style="margin-top:10px"><div style="font-size:12.5px;color:var(--mut)">${t(k==="archives"?"pArchives":"pMods")}</div>
 <div class="toolbar" style="margin-top:4px"><div class="search"><input id="p_${k}" value="${esc(S.paths[k])}"></div></div>
 <div class="toolbar" style="margin-top:6px"><button class="btn pri" style="padding:5px 10px" onclick="setDir('${k}',false)">${t("pSave")}</button><button class="btn" style="padding:5px 10px" onclick="setDir('${k}',true)">${t("pSaveMove")}</button>
 <button class="btn" style="padding:5px 10px" onclick="run('set_${k}','default')">${t("pDefault")}</button><a class="link" onclick="api('/api/open',{what:'${k}'})">📂</a></div></div>`).join("")}
 <div style="font-size:12px;color:var(--mut);margin-top:8px">${t("pHint")}</div>
 <div class="kv mono" style="margin-top:10px">${["root","output","profile"].map(k=>`<span class="k">${k}</span><a class="link" style="word-break:break-all" onclick="api('/api/open',{what:'${k==="root"?"output":k}'})">${esc(S.paths[k])}</a>`).join("")}</div></div>
 <div class="box"><b>${t("nexus")}</b><div style="margin:6px 0">${S.nexus?`${t("nxLogged")} <b>${esc(S.nexus.name)}</b> (${t(S.nexus.premium?"nxPremium":"nxFree")})`:t("nxNone")}</div>
 ${S.nexus?`<button class="btn" onclick="nxLogout()">${t("nxLogout")}</button>`:`<div style="font-size:12.5px;color:var(--mut)">${t("nxKeyHelp")}</div><div class="toolbar" style="margin-top:6px"><div class="search"><input id="nxKey" type="password" placeholder="${t("nxKey")}"></div><button class="btn pri" onclick="nxLogin()">${t("nxLogin")}</button></div>`}
 <div style="margin-top:10px"><button class="btn" onclick="run('nxm_handler')">${t("nxHandler")}</button><div style="font-size:12px;color:var(--mut);margin-top:4px">${t("nxHandlerHelp")}</div></div>
 ${S.nexus&&S.nexus.premium?`<div class="toolbar" style="margin-top:10px"><button class="btn" onclick="nxDl()">${t("nxDownload")}</button>${S.collection?`<button class="btn" onclick="run('collection_download')">${t("nxColl")}</button><button class="btn" onclick="run('collection_download_all')">${t("nxCollAll")}</button>`:""}</div>`:""}</div>
 <div class="box"><b>${t("language")}</b><div style="margin-top:8px"><select onchange="run('lang',this.value)"><option value="uk" ${L==="uk"?"selected":""}>Українська</option><option value="en" ${L==="en"?"selected":""}>English</option></select></div>
 <div style="margin-top:14px"><button class="btn danger" onclick="quit()">${t("quit")}</button></div></div></div>`)}
function bindBackups(){document.querySelectorAll("[data-gb]").forEach(b=>b.onclick=()=>{const n=b.dataset.n,k=b.dataset.gb;
 if(k==="restore"&&!confirm(t("gbConfirm").replace("%s",n)))return;if(k==="delete"&&!confirm(t("gbDelConfirm").replace("%s",n)))return;
 run(k==="restore"?"restore_game":k==="verify"?"backup_verify":"backup_delete",n)})}
function renderMain(){const m=$("#main");let left;
 if(page==="home"||page==="mods")left=`<div class="col">${modsView()}</div>`;else left=`<div class="col">${{collection:collectionView,fomod:fomodView,plugins:pluginsView,deps:depsView,conflicts:conflictsView,deploy:deployView,backups:backupsView,settings:settingsView}[page]()}</div>`;
 m.innerHTML=left+`<div class="col rightcol">${statusCard()}${page==="home"||page==="mods"?detail():""}</div>`;bindStatus();if(page==="home"||page==="mods")bindMods();if(page==="backups")bindBackups()}
function render(){document.documentElement.lang=L;document.querySelectorAll("[data-i]").forEach(e=>e.textContent=t(e.dataset.i));$("#ver").textContent="v"+S.version;$("#langSel").value=L;$("#quitBtn").title=t("quit");nav();renderMain();jobLine()}
function jobLine(){const j=S&&S.job;$("#dot").className="dot"+(running?" run":"");$("#jobtxt").textContent=running?t("working")+" "+(j?j.args.join(" "):""):(j&&j.rc?"rc="+j.rc:t("idle"))}
function menu(ev,items){ev.stopPropagation();closeMenu();const d=document.createElement("div");d.className="menu";d.id="menu";
 d.innerHTML=items.map((it,i)=>`<button data-i="${i}">${esc(it[0])}</button>`).join("");document.body.appendChild(d);
 const r=ev.target.getBoundingClientRect();d.style.top=Math.min(r.bottom+4,innerHeight-d.offsetHeight-8)+"px";d.style.left=Math.min(r.left,innerWidth-d.offsetWidth-8)+"px";
 d.querySelectorAll("button").forEach(b=>b.onclick=()=>{closeMenu();items[+b.dataset.i][1]()})}
function closeMenu(){const m=$("#menu");if(m)m.remove()}document.addEventListener("click",closeMenu);
async function run(action,value){
 if(action==="lang"||action==="profile"){const r=await api("/api/set",{[action]:value});if(r.error){alert(r.error==="busy"?t("busy"):r.error)}await refresh();return}const r=await api("/api/run",{action,value});if(r.error){alert(r.error==="busy"?t("busy"):r.error);return}running=true;jobLine();renderMain()}
async function launch(){const r=await api("/api/launch",{});if(r.result==="no-steam")alert(t("launchFail"));if(r.result==="running")alert(t("gameRunning"))}
function setDir(k,move){const v=$("#p_"+k).value.trim();if(!v)return;if(move&&!confirm(t("pMoveConfirm")))return;run("set_"+k+(move?"_move":""),v)}
async function newProfColl(){const n=$('#newProf').value.trim(),s=$('#newProfColl').value.trim();if(!n||!s){alert(t("npNeed"));return}
 const r=await api("/api/profile_new_coll",{name:n,source:s});if(r.error){alert(r.error==="busy"?t("busy"):r.error);return}running=true;jobLine();renderMain()}
async function nxLogin(){const k=$("#nxKey").value.trim();if(!k)return;const r=await api("/api/nexus_login",{key:k});if(r.error)alert(t("nxLoginFail"));await refresh()}
async function nxLogout(){await api("/api/nexus_logout",{});await refresh()}
function nxDl(){const v=prompt(t("nxAsk"));if(v&&v.trim())run("nexus_download",v.trim())}
async function quit(){await api("/api/quit",{});document.body.innerHTML=`<div class="empty" style="padding:80px">${t("closed")}</div>`}
$("#langSel").onchange=e=>run("lang",e.target.value);$("#quitBtn").onclick=quit;
(()=>{const lg=document.querySelector(".log"),b=$("#logTog");let m=false;try{m=localStorage.getItem("smeLogMin")==="1"}catch(e){}
 const ap=()=>{lg.classList.toggle("min",m);b.textContent=m?"▴":"▾"};ap();b.onclick=()=>{m=!m;try{localStorage.setItem("smeLogMin",m?"1":"0")}catch(e){}ap()}})();
async function refresh(){S=await api("/api/state");L=S.lang||"uk";running=!!(S.job&&S.job.running);if(sel&&!S.mods.find(m=>m.name===sel))sel=null;if(!sel&&S.mods.length)sel=S.mods[0].name;render()}
async function pollLog(){try{const r=await api("/api/log?from="+logFrom);const pre=$("#log");if(r.lines.length){const atEnd=pre.scrollTop+pre.clientHeight>=pre.scrollHeight-20;
 pre.textContent+=(pre.textContent?"\n":"")+r.lines.join("\n");if(pre.textContent.length>200000)pre.textContent=pre.textContent.slice(-150000);if(atEnd)pre.scrollTop=pre.scrollHeight}
 logFrom=r.next;const was=running;running=!!(r.job&&r.job.running);if(was&&!running)await refresh();else jobLine()}catch(e){}setTimeout(pollLog,running?500:1500)}
setInterval(()=>api("/api/beat",{}).catch(()=>{}),5000);api("/api/beat",{});
refresh().then(pollLog);
</script></body></html>"""


def _gui_trees() -> str:
    out, x = [], 0
    import random
    rnd = random.Random(7)
    while x < 1600:
        h = rnd.randint(14, 30)
        y = 150 - rnd.randint(0, 8)
        out.append(f'<path d="M{x} {y} L{x + h * 0.35:.0f} {y - h} L{x + h * 0.7:.0f} {y}Z"/>')
        x += rnd.randint(6, 22)
    return "".join(out)


GUI_PAGE = GUI_PAGE.replace("%TREES%", _gui_trees())

# ===================================================================================
# Native window (GTK 3 via PyGObject, present on SteamOS). Same backend (GuiApp) and the same
# translations as the web page; every action still runs this script as a subprocess.
# ===================================================================================
def gui_i18n() -> dict:
    """The I18N table of the web page, parsed from its JavaScript literal (one table for both GUIs)."""
    a = GUI_PAGE.index("const I18N=") + len("const I18N=")
    b = GUI_PAGE.index("\nlet S=null", a)
    js = GUI_PAGE[a:b].rstrip().rstrip(";")
    out, i, n = [], 0, len(js)
    while i < n:  # quote bare keys outside strings: {key:"..."} -> {"key":"..."}
        c = js[i]
        if c == '"':
            j = i + 1
            while js[j] != '"':
                j += 2 if js[j] == "\\" else 1
            out.append(js[i:j + 1])
            i = j + 1
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (js[j].isalnum() or js[j] == "_"):
                j += 1
            out.append('"' + js[i:j] + '"')
            i = j
        else:
            out.append(c)
            i += 1
    return json.loads("".join(out))

def gtk_available() -> bool:
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk  # noqa: F401
        return True
    except Exception:
        return False

GTK_CSS = b"""
.sme-card { border: 1px solid alpha(currentColor, 0.18); border-radius: 10px; padding: 10px; }
.sme-title { font-size: 15pt; font-weight: bold; }
.sme-h2 { font-size: 13pt; font-weight: bold; }
.sme-muted { color: #8a9ab5; }
.sme-ok { color: #1fbf73; }
.sme-warn { color: #e0a43a; }
.sme-bad { color: #e2554f; }
.sme-play { background-image: none; background-color: #1fbf73; color: #04150c; font-weight: bold; }
.sme-log { font-family: monospace; font-size: 9pt; }
"""

def gtk_run(engine, engine_path: Path, output: str = "mod_engine_output", on_ready=None) -> int:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk, GLib, Gdk, Pango
    os.chdir(engine_path.parent)
    app = GuiApp(engine, engine_path, output)
    app.out.mkdir(parents=True, exist_ok=True)
    I18N = gui_i18n()
    esc = GLib.markup_escape_text

    class W:
        S: dict = {}
        sel: str | None = None
        log_from = 0
        was_running = False
        filter_text = ""
        filter_cat = ""
        building = False
        last_click = (0, 0)

    def t(k: str) -> str:
        return (I18N.get(W.S.get("lang") or engine._LANG) or I18N["uk"]).get(k, k)

    settings = Gtk.Settings.get_default()
    if settings is not None:
        settings.set_property("gtk-application-prefer-dark-theme", True)
    css = Gtk.CssProvider()
    css.load_from_data(GTK_CSS)
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    win = Gtk.Window(title="Skyrim Mod Engine")
    win.set_default_size(1280, 800)

    # ---------------------------------------------------------------- helpers
    def label(text="", cls=None, markup=False, wrap=True, xalign=0.0, sel=False):
        l = Gtk.Label()
        (l.set_markup if markup else l.set_text)(text)
        l.set_xalign(xalign)
        l.set_line_wrap(wrap)
        if wrap:
            l.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        l.set_selectable(sel)
        for c in (cls or "").split():
            l.get_style_context().add_class(c)
        return l

    def button(text, cb, cls=None):
        b = Gtk.Button(label=text)
        b.connect("clicked", lambda *_: cb())
        for c in (cls or "").split():
            b.get_style_context().add_class(c)
        return b

    def hbox(*w, spacing=8):
        b = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)
        for x in w:
            b.pack_start(x, False, False, 0)
        return b

    def vbox(spacing=8):
        return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)

    def card(child):
        f = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        f.get_style_context().add_class("sme-card")
        f.pack_start(child, True, True, 0)
        return f

    def clear(box):
        for c in box.get_children():
            box.remove(c)

    # Popovers instead of GtkComboBox/GtkMenu: those draw scroll arrows and jump around on some
    # desktops (seen on SteamOS/KDE); a popover is a plain list attached to its button.
    def popover_menu(relative, items):
        pop = Gtk.Popover.new(relative)
        box = vbox(0)
        box.set_border_width(4)
        for text, cb in items:
            b = Gtk.ModelButton()
            b.set_property("text", text)
            b.connect("clicked", lambda _b, f=cb: GLib.idle_add(lambda: (f(), False)[1]))
            box.pack_start(b, False, False, 0)
        box.show_all()
        pop.add(box)
        return pop

    def menu_button(text, items, cls=None):
        mb = Gtk.MenuButton()
        if text is not None:
            mb.set_label(text)
        mb.set_popover(popover_menu(mb, items))
        for c in (cls or "").split():
            mb.get_style_context().add_class(c)
        return mb

    def choice(options, active, on_change):
        """A dropdown made of a button + popover. options: [(id, label)]."""
        mb = Gtk.MenuButton()
        lab = label(dict(options).get(active, options[0][1] if options else ""), wrap=False)
        row = Gtk.Box(spacing=6)
        row.pack_start(lab, True, True, 0)
        row.pack_end(Gtk.Image.new_from_icon_name("pan-down-symbolic", Gtk.IconSize.BUTTON), False, False, 0)
        mb.add(row)
        def pick(i, l):
            lab.set_text(l)
            mb.choice_id = i
            on_change(i)
        mb.set_popover(popover_menu(mb, [(l, (lambda i=i, l=l: pick(i, l))) for i, l in options]))
        mb.choice_id = active
        return mb

    def confirm(text) -> bool:
        d = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.OK_CANCEL, text=text)
        r = d.run()
        d.destroy()
        return r == Gtk.ResponseType.OK

    def info(text):
        d = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.INFO,
                              buttons=Gtk.ButtonsType.OK, text=text)
        d.run()
        d.destroy()

    def ask_text(text, password=False) -> str | None:
        d = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.QUESTION,
                              buttons=Gtk.ButtonsType.OK_CANCEL, text=text)
        e = Gtk.Entry()
        e.set_visibility(not password)
        e.set_activates_default(True)
        d.get_message_area().pack_end(e, False, False, 0)
        d.set_default_response(Gtk.ResponseType.OK)
        d.show_all()
        r = d.run()
        v = e.get_text().strip()
        d.destroy()
        return v if r == Gtk.ResponseType.OK and v else None

    def open_url(url):
        try:
            Gtk.show_uri_on_window(win, url, Gdk.CURRENT_TIME)
        except Exception:
            if shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", url], start_new_session=True)

    def fmt_size(b):
        return human_size(int(b or 0))

    def fmt_date(s):
        try:
            return datetime.fromisoformat(s).strftime("%d.%m.%Y %H:%M") if s else "—"
        except Exception:
            return s or "—"

    def run(action, value=None):
        if action in GUI_VALUE_ACTIONS:
            base, flag = GUI_VALUE_ACTIONS[action]
            GUI_ACTIONS[action] = []
            err = app.start(action, [flag, value or ""] + base)
        else:
            err = app.start(action)
        if err:
            info(t("busy") if err == "busy" else err)
        poll()

    def switch_profile(name):
        err = app.set_profile(name)
        if err:
            info(t("busy") if err == "busy" else err)
        refresh()

    def launch():
        r = app.launch()
        if r == "no-steam":
            info(t("launchFail"))
        elif r == "running":
            info(t("gameRunning"))

    # ---------------------------------------------------------------- layout
    root = vbox(0)
    win.add(root)

    # banner: an original mountain scene drawn with cairo (no game artwork)
    banner = Gtk.DrawingArea()
    banner.set_size_request(-1, 86)

    def draw_banner(w, cr):
        width, height = w.get_allocated_width(), w.get_allocated_height()
        import cairo
        g = cairo.LinearGradient(0, 0, 0, height)
        g.add_color_stop_rgb(0, 0.16, 0.31, 0.53)
        g.add_color_stop_rgb(1, 0.55, 0.71, 0.85)
        cr.set_source(g)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        def ridge(points, rgb):
            cr.set_source_rgb(*rgb)
            cr.move_to(0, height)
            for x, y in points:
                cr.line_to(x * width, y * height)
            cr.line_to(width, height)
            cr.close_path()
            cr.fill()
        ridge([(0, .8), (.08, .45), (.14, .7), (.22, .25), (.29, .62), (.37, .2), (.45, .66), (.52, .33), (.6, .72), (.67, .22), (.74, .62),
               (.81, .3), (.88, .66), (.95, .4), (1, .58)], (0.42, 0.53, 0.66))
        ridge([(0, .9), (.1, .66), (.18, .84), (.28, .6), (.37, .86), (.47, .64), (.58, .88), (.65, .68), (.75, .9), (.85, .66), (.94, .86), (1, .7)],
              (0.18, 0.28, 0.4))
        fade = cairo.LinearGradient(0, 0, width, 0)
        fade.add_color_stop_rgba(0, 0.04, 0.07, 0.13, 0.95)
        fade.add_color_stop_rgba(0.45, 0.04, 0.07, 0.13, 0.45)
        fade.add_color_stop_rgba(1, 0.04, 0.07, 0.13, 0.15)
        cr.set_source(fade)
        cr.rectangle(0, 0, width, height)
        cr.fill()
        cr.set_source_rgb(0.92, 0.95, 0.98)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(24)
        cr.move_to(22, 42)
        cr.show_text("Skyrim Mod Engine")
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(13)
        cr.set_source_rgb(0.75, 0.81, 0.9)
        cr.move_to(22, 64)
        cr.show_text(f"Mod Loader & Manager · v{W.S.get('version', '?')} · {t('tagline')}")
    try:
        gi.require_foreign("cairo")  # python-cairo bridge; without it a plain title bar is used
        import cairo  # noqa: F401
        banner.connect("draw", draw_banner)
        root.pack_start(banner, False, False, 0)
    except Exception:
        banner = Gtk.Label()
        banner.set_xalign(0.0)
        banner.set_margin_start(16)
        banner.set_margin_top(8)
        banner.set_margin_bottom(8)
        banner.get_style_context().add_class("sme-title")
        root.pack_start(banner, False, False, 0)

    paned = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL)
    root.pack_start(paned, True, True, 0)
    body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
    paned.pack1(body, True, False)

    stack = Gtk.Stack()
    stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
    side = Gtk.StackSidebar()
    side.set_stack(stack)
    side.set_size_request(200, -1)
    body.pack_start(side, False, False, 0)
    center = vbox(8)
    center.set_border_width(10)
    center.pack_start(stack, True, True, 0)
    body.pack_start(center, True, True, 0)
    right = vbox(10)
    right.set_border_width(10)
    right.set_size_request(300, -1)
    body.pack_start(right, False, False, 0)

    # log at the bottom
    logbox = vbox(4)
    logbox.set_border_width(6)
    loghead = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    loglabel = label("", "sme-h2", wrap=False)
    spinner = Gtk.Spinner()
    jobl = label("", "sme-muted", wrap=False)
    loghead.pack_start(loglabel, False, False, 0)
    loghead.pack_end(jobl, False, False, 0)
    loghead.pack_end(spinner, False, False, 0)
    logbox.pack_start(loghead, False, False, 0)
    logview = Gtk.TextView()
    logview.set_editable(False)
    logview.set_monospace(True)
    logview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    logview.get_style_context().add_class("sme-log")
    logsc = Gtk.ScrolledWindow()
    logsc.add(logview)
    logbox.pack_start(logsc, True, True, 0)
    paned.pack2(logbox, False, True)
    paned.set_position(560)

    pages: dict[str, Gtk.Box] = {}
    PAGE_KEYS = ["mods", "fomod", "collection", "plugins", "deps", "conflicts", "deploy", "backups", "settings"]
    for k in PAGE_KEYS:
        b = vbox(10)
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)  # never let a page widen the window
        if k == "mods":
            stack.add_titled(b, k, k)  # the mods page manages its own scrolling (the list)
        else:
            sc.add(b)
            stack.add_titled(sc, k, k)
        pages[k] = b

    # ---------------------------------------------------------------- mods page (built once)
    mods_page = pages["mods"]
    mods_banners = vbox(6)
    mods_page.pack_start(mods_banners, False, False, 0)
    tb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    add_btn = Gtk.MenuButton()
    extract_btn = Gtk.Button()
    fomod_btn = Gtk.Button()
    search = Gtk.SearchEntry()
    catholder = Gtk.Box()
    for w_ in (add_btn, extract_btn, fomod_btn):
        tb.pack_start(w_, False, False, 0)
    mods_page.pack_start(tb, False, False, 0)
    tb2 = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    tb2.pack_start(search, True, True, 0)
    tb2.pack_start(catholder, False, False, 0)
    mods_page.pack_start(tb2, False, False, 0)
    extract_btn.connect("clicked", lambda *_: run("scan"))
    fomod_btn.connect("clicked", lambda *_: run("fomod_auto"))
    add_btn.get_style_context().add_class("suggested-action")

    store = Gtk.ListStore(int, str, str, str, str, bool, str)  # index, name, markup, category, version, enabled, status
    filt = store.filter_new()

    def visible(model, it, _):
        name, cat, en = model[it][1], model[it][3], model[it][5]
        if W.filter_text and W.filter_text.lower() not in name.lower():
            return False
        if W.filter_cat == "__on":
            return en
        if W.filter_cat == "__off":
            return not en
        return not W.filter_cat or W.filter_cat == cat
    filt.set_visible_func(visible)
    tree = Gtk.TreeView(model=store)
    tree.set_headers_visible(True)
    tree.set_enable_search(False)
    sc_tree = Gtk.ScrolledWindow()
    sc_tree.add(tree)
    mods_page.pack_start(sc_tree, True, True, 0)
    cols = {}
    for i, (key, ci, expand) in enumerate((("#", 0, False), ("colName", 2, True), ("colCat", 3, False), ("colVer", 4, False))):
        r = Gtk.CellRendererText()
        if key == "colName":
            r.set_property("ellipsize", Pango.EllipsizeMode.END)
            c = Gtk.TreeViewColumn("", r, markup=ci)
        else:
            c = Gtk.TreeViewColumn("", r, text=ci)
        c.set_expand(expand)
        c.set_resizable(True)
        tree.append_column(c)
        cols[key] = c
    rt = Gtk.CellRendererToggle()
    ct = Gtk.TreeViewColumn("", rt, active=5)
    rs = Gtk.CellRendererText()
    ct.pack_start(rs, True)
    ct.add_attribute(rs, "text", 6)
    tree.append_column(ct)
    cols["colStatus"] = ct

    def current_model_rows():
        m = tree.get_model()
        return [m[i][1] for i in range(len(m))]

    def on_toggle(_r, path):
        m = tree.get_model()
        name, en = m[path][1], m[path][5]
        if app.job and app.job.get("running"):
            return info(t("busy"))
        app.toggle(name, not en)
        refresh()
    rt.connect("toggled", on_toggle)

    def on_drag_end(*_):
        if W.building or tree.get_model() is not store:
            return
        new = current_model_rows()
        if new != [m["name"] for m in W.S.get("mods", [])]:
            app.edit_modlist(lambda order, disabled: (new, disabled))
            refresh()
    tree.connect("drag-end", on_drag_end)

    def selected_name():
        m, it = tree.get_selection().get_selected()
        return m[it][1] if it else None

    def on_sel(*_):
        if not W.building:
            W.sel = selected_name()
            render_right()
    tree.get_selection().connect("changed", on_sel)

    def move(name, to):
        app.move(name, max(0, to))
        refresh()

    def mod_menu(name):
        m = next((x for x in W.S["mods"] if x["name"] == name), None)
        if not m:
            return
        pop = popover_menu(tree, ((t("up"), lambda: move(name, m["index"] - 1)), (t("down"), lambda: move(name, m["index"] + 1)),
                                  (t("top"), lambda: move(name, 0)), (t("bottom"), lambda: move(name, len(W.S["mods"]))),
                                  (t("openFolder"), lambda: app.open_path("mod:" + name))))
        x, y = W.last_click
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        pop.set_pointing_to(rect)
        pop.popup()

    def on_press(_w, ev):
        if ev.button == 3:
            W.last_click = tree.convert_bin_window_to_widget_coords(int(ev.x), int(ev.y))
            res = tree.get_path_at_pos(int(ev.x), int(ev.y))
            if res:
                tree.get_selection().select_path(res[0])
                mod_menu(tree.get_model()[res[0]][1])
            return True
        return False
    tree.connect("button-press-event", on_press)

    def on_filter(*_):
        W.filter_text = search.get_text().strip()
        use_filter = bool(W.filter_text or W.filter_cat)
        tree.set_model(filt if use_filter else store)
        tree.set_reorderable(not use_filter)  # drag-and-drop order only on the full list
        filt.refilter()
    search.connect("search-changed", on_filter)
    def set_cat(i):
        W.filter_cat = i or ""
        on_filter()

    def fill_mods():
        S = W.S
        W.building = True
        store.clear()
        for m in S["mods"]:
            sub = f'{m["files"]} {t("files")}' + (f' · {len(m["plugins"])} {t("pl")}' if m["plugins"] else "") + f' · {m.get("size") or ""}'
            store.append([m["index"] + 1, m["name"], f'<b>{esc(m["name"])}</b>\n<small><span foreground="#8a9ab5">{esc(sub)}</span></small>',
                          t("cat_" + m["category"]), m.get("version") or "—", m["enabled"], t("active" if m["enabled"] else "off")])
        opts = [("", t("allCats")), ("__on", t("onlyOn")), ("__off", t("onlyOff"))] + \
            [(c, t("cat_" + c)) for c in sorted({m["category"] for m in S["mods"]})]
        if W.filter_cat not in dict(opts):
            W.filter_cat = ""
        clear(catholder)
        catholder.pack_start(choice(opts, W.filter_cat, set_cat), False, False, 0)
        catholder.show_all()
        W.building = False
        on_filter()
        if W.sel:
            m = tree.get_model()
            for i in range(len(m)):
                if m[i][1] == W.sel:
                    tree.get_selection().select_path(Gtk.TreePath(i))
                    tree.scroll_to_cell(Gtk.TreePath(i), None, False, 0, 0)
                    break

    # ---------------------------------------------------------------- right column
    def protection_lines():
        S = W.S
        pr = S.get("protection") or {}
        yes, no = '<span foreground="#1fbf73">✔</span>', '<span foreground="#e0a43a">✖</span>'
        bk = len(S.get("game_backups") or [])
        return "\n".join([f'{yes if S.get("baseline") else no} {esc(t("prList"))}',
                          f'{yes if pr.get("incremental") else no} {esc(t("prInc"))}' + (f' ({pr.get("store_files")}, {fmt_size(pr.get("store_size"))})' if pr.get("incremental") and pr.get("store_files") else ("" if pr.get("incremental") else ": " + esc(t("prOff")))),
                          f'{yes if bk else no} {bk} {esc(t("prBackups"))}'])

    def render_right():
        clear(right)
        S = W.S
        d = S.get("deployed")
        on = [m for m in S["mods"] if m["enabled"]]
        running = bool(app.job and app.job.get("running"))
        need_deploy = (not d) or d["profile"] != S["profile"] or S.get("pending") or not d.get("complete")
        if running:
            title, cls = t("working"), "sme-warn"
        elif not d:
            title, cls = t("notdeployed"), "sme-warn"
        elif d["profile"] != S["profile"]:
            title, cls = t("other") + ": " + d["profile"], "sme-warn"
        elif not d.get("complete"):
            title, cls = t("incomplete"), "sme-bad"
        elif S.get("pending"):
            title, cls = t("pending"), "sme-warn"
        else:
            title, cls = t("ready"), "sme-ok"
        box = vbox(6)
        box.pack_start(label("● " + title, "sme-h2 " + cls), False, False, 0)
        grid = Gtk.Grid(column_spacing=10, row_spacing=4)
        prof = choice([(p, p) for p in S["profiles"]], S["profile"], lambda i: None if i == W.S["profile"] else switch_profile(i))
        prot = Gtk.Button()
        protl = label(protection_lines(), markup=True, wrap=True)
        prot.add(protl)
        prot.set_relief(Gtk.ReliefStyle.NONE)
        prot.connect("clicked", lambda *_: stack.set_visible_child_name("backups"))
        rows = [(t("profile"), prof), (t("enabledMods"), label(f"{len(on)} / {len(S['mods'])}")),
                (t("totalSize"), label(fmt_size(sum(m.get("size_bytes") or 0 for m in on)))),
                (t("lastDeploy"), label(fmt_date(d["at"]) + (f" ({d['profile']})" if d and d["profile"] != S["profile"] else "") if d else "—")),
                (t("protect"), prot)]
        for i, (k, w_) in enumerate(rows):
            kl = label(k + ":", "sme-muted", wrap=True)
            kl.set_max_width_chars(14)
            grid.attach(kl, 0, i, 1, 1)
            grid.attach(w_, 1, i, 1, 1)
        box.pack_start(grid, False, False, 0)
        play = Gtk.Button(label="▶ " + t("deployPlay" if need_deploy else "play"))
        play.get_style_context().add_class("sme-play")
        play.set_sensitive(not running)
        play.connect("clicked", lambda *_: run("deploy_launch") if need_deploy else launch())
        more = menu_button(None, ((t("deployPlay"), lambda: run("deploy_launch")), (t("playOnly"), launch), (t("dry"), lambda: run("dry"))))
        pb = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        pb.get_style_context().add_class("linked")
        pb.pack_start(play, True, True, 0)
        pb.pack_start(more, False, False, 0)
        box.pack_start(pb, False, False, 4)
        right.pack_start(card(box), False, False, 0)
        if stack.get_visible_child_name() == "mods":
            right.pack_start(card(detail_box()), True, True, 0)
        right.show_all()

    def detail_box():
        S = W.S
        m = next((x for x in S["mods"] if x["name"] == W.sel), None)
        outer = vbox(6)
        if not m:
            outer.pack_start(label(t("noSel"), "sme-muted"), False, False, 0)
            return outer
        b = vbox(6)
        b.pack_start(label(f'<b>{esc(m["name"])}</b>' + (f'\n<span foreground="#8a9ab5">v{esc(m["version"])}</span>' if m.get("version") else ""), markup=True), False, False, 0)
        kv = [(t("category"), t("cat_" + m["category"])), (t("size"), m.get("size") or ""), (t("filesCount"), str(m["files"])), (t("order"), str(m["index"] + 1))]
        if m.get("archive"):
            kv.append((t("archive"), m["archive"]))
        b.pack_start(label("\n".join(f'<span foreground="#8a9ab5">{esc(k)}:</span> {esc(v)}' for k, v in kv), markup=True), False, False, 0)
        links = hbox(button(t("openFolder"), lambda: app.open_path("mod:" + m["name"])))
        if m.get("nexus_id"):
            links.pack_start(button(t("nexus"), lambda: open_url(f"https://www.nexusmods.com/skyrimspecialedition/mods/{m['nexus_id']}")), False, False, 0)
        b.pack_start(links, False, False, 0)
        def sec(title, items):
            if items:
                b.pack_start(label(f"<b>{esc(title)}</b>\n" + "\n".join("• " + esc(str(x)) for x in items), markup=True), False, False, 0)
        sec(t("pluginsL"), m["plugins"])
        sec(t("sksePl"), m["skse_plugins"])
        sec(t("bsa"), m["bsa"])
        if m.get("fomod_steps"):
            sec(t("fomodChoice"), [f'{g["group"].rstrip(":")}: {", ".join(g.get("selected") or []) or "—"}' for s_ in m["fomod_steps"] for g in s_.get("groups", [])])
        sec(t("dependsOn"), m["deps"])
        sec(t("neededBy"), m["rdeps"])
        probs = [p for p in S.get("problems", []) if f"«{m['name']}»" in p]
        b.pack_start(label("\n".join("⚠ " + esc(p) for p in probs) if probs else "✔ " + esc(t("allDepsOk")), "sme-bad" if probs else "sme-ok", markup=True), False, False, 0)
        sec(t("wins"), [f"{n} ({c})" for n, c in m["wins"]])
        sec(t("loses"), [f"{n} ({c})" for n, c in m["loses"]])
        sc = Gtk.ScrolledWindow()
        sc.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sc.add(b)
        outer.pack_start(sc, True, True, 0)
        return outer

    # ---------------------------------------------------------------- other pages
    def page_header(p, title, hint=None):
        p.pack_start(label(title, "sme-title"), False, False, 0)
        if hint:
            p.pack_start(label(hint, "sme-muted"), False, False, 0)

    def render_mod_banners():
        clear(mods_banners)
        S = W.S
        if not (S.get("protection") or {}).get("asked"):
            mods_banners.pack_start(card(hbox(label("🛡 " + t("psTitle")), button(t("gbBannerBtn"), lambda: stack.set_visible_child_name("backups"), "suggested-action"))), False, False, 0)
        d = S.get("deployed")
        if S.get("pending") and d and d["profile"] == S["profile"]:
            mods_banners.pack_start(card(hbox(label("⚠ " + t("pendingBanner"), "sme-warn"), button(t("deployNow"), lambda: run("deploy"), "suggested-action"))), False, False, 0)
        if not S["mods"]:
            mods_banners.pack_start(label(t("noMods"), "sme-muted"), False, False, 0)
        mods_banners.show_all()

    def render_fomod(p):
        S = W.S
        page_header(p, t("fomodPage"), t("fomodHint"))
        p.pack_start(hbox(button(t("installFomod"), lambda: run("fomod_auto"), "suggested-action"), button(t("reinstallAuto"), lambda: run("fomod_redo_auto"))), False, False, 0)
        for f in S["fomods"]:
            lines = [f'<b>{esc(f["name"])}</b>', f'<span foreground="#8a9ab5">{esc(t("installedTo") + ": " + f["installed_to"]) if f["installed_to"] else esc(t("notInstalled"))}'
                     + (f' · {esc(t("modeL"))}: {esc(f["mode"])}' if f.get("mode") else "") + "</span>"]
            for s_ in f.get("steps") or []:
                for g in s_.get("groups", []):
                    lines.append(f'• {esc(g["group"].rstrip(":"))}: {esc(", ".join(g.get("selected") or []) or "—")}')
            p.pack_start(card(label("\n".join(lines), markup=True)), False, False, 0)
        if S.get("attention"):
            p.pack_start(label(t("attention"), "sme-h2"), False, False, 0)
            for a in S["attention"]:
                p.pack_start(label(f'{a["name"]} — {a["layout"]}' + (": " + ", ".join(a["options"][:6]) if a["options"] else ""), "sme-warn"), False, False, 0)

    def render_collection(p):
        S = W.S
        c = S.get("collection")
        pr = bool(S.get("nexus") and S["nexus"]["premium"])
        page_header(p, (c or {}).get("name") or t("collection"), (t("collBy") + " " + (c.get("author") or "?")) if c else t("collNone"))
        if S.get("nexus"):
            e = Gtk.Entry()
            e.set_placeholder_text(t("collLink"))
            row = Gtk.Box(spacing=8)
            row.pack_start(e, True, True, 0)
            row.pack_start(button(t("collFetch"), lambda: e.get_text().strip() and run("collection_nexus", e.get_text().strip()), "suggested-action"), False, False, 0)
            p.pack_start(row, False, False, 0)
        else:
            p.pack_start(label(t("collNeedLogin"), "sme-muted"), False, False, 0)
        if not c:
            return
        req = [m for m in c["mods"] if not m["optional"]]
        opt = [m for m in c["mods"] if m["optional"]]
        miss = [m for m in c["mods"] if not m["have"]]
        p.pack_start(label(f'{t("collFound")}: {t("collReq")} {sum(m["have"] for m in req)}/{len(req)}, {t("collOpt")} {sum(m["have"] for m in opt)}/{len(opt)}'), False, False, 0)
        row = hbox(button(t("collScan"), lambda: run("scan")))
        if pr and miss:
            row.pack_start(button(t("nxColl"), lambda: run("collection_download"), "suggested-action"), False, False, 0)
            row.pack_start(button(t("nxCollAll"), lambda: run("collection_download_all")), False, False, 0)
        p.pack_start(row, False, False, 0)
        if miss and not pr:
            p.pack_start(label(t("collFreeHint"), "sme-muted"), False, False, 0)
        if miss:
            p.pack_start(label(f'{t("collMissing")}: {len(miss)}', "sme-h2"), False, False, 0)
            lb = Gtk.ListBox()
            lb.set_selection_mode(Gtk.SelectionMode.NONE)
            for m in miss[:400]:
                r = Gtk.Box(spacing=8)
                r.set_border_width(4)
                r.pack_start(label(f'{m["name"]} {m.get("version") or ""}' + (f'  ({t("optional")})' if m["optional"] else "")), True, True, 0)
                if m.get("mod_id") and m.get("file_id"):
                    url = f'https://www.nexusmods.com/skyrimspecialedition/mods/{int(m["mod_id"])}?tab=files&file_id={int(m["file_id"])}&nmm=1'
                    r.pack_end(button(t("collDl"), lambda u=url: open_url(u)), False, False, 0)
                lb.add(r)
            p.pack_start(lb, False, False, 0)

    def render_plugins(p):
        d = W.S.get("deployed")
        page_header(p, t("pluginsPage"), f'{d["profile"]} · {fmt_date(d["at"])} · {len(d["plugins"])}' if d else t("noDeploy"))
        if d:
            p.pack_start(hbox(button(t("lootUpdate"), lambda: run("loot_update"))), False, False, 0)
            p.pack_start(label("\n".join(f"{i}. {x}" for i, x in enumerate(d["plugins"], 1)), "sme-log", sel=True), False, False, 0)

    def render_deps(p):
        S = W.S
        page_header(p, t("depsPage"))
        p.pack_start(label(t("problems"), "sme-h2"), False, False, 0)
        p.pack_start(label("\n".join("⚠ " + x for x in S["problems"]) if S["problems"] else "✔ " + t("noProblems"), "sme-bad" if S["problems"] else "sme-ok"), False, False, 0)
        for m in S["mods"]:
            if m["deps"]:
                p.pack_start(card(label(f'<b>{esc(m["name"])}</b>\n<span foreground="#8a9ab5">{esc(t("dependsOn"))}:</span> ' + esc(", ".join(m["deps"])), markup=True)), False, False, 0)

    def render_conflicts(p):
        c = W.S["conflicts"]
        page_header(p, t("conflictsPage"), None if c["computed"] else t("notComputed"))
        p.pack_start(hbox(button(t("recompute"), lambda: run("conflicts"), "suggested-action")), False, False, 0)
        if not c["computed"]:
            return
        p.pack_start(label(f'{c["pairs_total"]} · {t("identical")}: {c["identical"]}', "sme-muted"), False, False, 0)
        if c["duplicate_plugins"]:
            p.pack_start(label(t("dupPlugins"), "sme-h2 sme-bad"), False, False, 0)
            p.pack_start(label("\n".join(f"{pl}: {', '.join(ms)}" for pl, ms in c["duplicate_plugins"])), False, False, 0)
        st = Gtk.ListStore(str, str, int, str)
        for x in c["pairs"]:
            st.append([x["winner"], x["loser"], x["count"], ", ".join(f"{k}: {v}" for k, v in (x.get("by_severity") or {}).items())])
        tv = Gtk.TreeView(model=st)
        for i, k in enumerate(("winner", "loser", "count", "")):
            col = Gtk.TreeViewColumn(t(k) if k else "", Gtk.CellRendererText(), text=i)
            col.set_resizable(True)
            col.set_expand(i < 2)
            tv.append_column(col)
        p.pack_start(tv, False, False, 0)

    def render_deploy(p):
        S = W.S
        d, k = S.get("deployed"), S.get("skse")
        page_header(p, t("deployPage"))
        row = hbox(button(f'{t("deployBtn")} «{S["profile"]}»', lambda: run("deploy"), "suggested-action"), button(t("dry"), lambda: run("dry")),
                   button(t("skseBtn"), lambda: run("skse")))
        if S.get("nexus"):
            row.pack_start(button(t("skseInstall"), lambda: run("skse_install")), False, False, 0)
        row.pack_start(button(t("undeployBtn"), lambda: confirm(t("confirmUndeploy")) and run("undeploy"), "destructive-action"), False, False, 0)
        p.pack_start(row, False, False, 0)
        lines = [f'{t("deployedProfile")}: {d["profile"] if d else "—"}', f'{t("lastDeploy")}: {fmt_date(d["at"]) if d else "—"}', f'{t("filesDeployed")}: {d["files"] if d else "—"}']
        p.pack_start(card(label("\n".join(lines) + (("\n" + t("incomplete")) if d and not d.get("complete") else ""))), False, False, 0)
        if k:
            cnt: dict[str, int] = {}
            for x in k.get("plugins") or []:
                cnt[x["status"]] = cnt.get(x["status"], 0) + 1
            sk = k.get("skse") or {}
            txt = [f'<b>{esc(t("skseTitle"))}</b>', f'{esc(t("gameVer"))}: {esc(k.get("game_version") or "?")}',
                   f'SKSE: <span foreground="{"#1fbf73" if sk.get("matches_game") else "#e0a43a"}">{esc(", ".join(sk.get("runtime_dlls") or []) or "—")}</span>',
                   f'{esc(t("sksePlugins"))}: {esc(", ".join(f"{a}: {b}" for a, b in cnt.items()) or "—")}']
            txt += ['<span foreground="#e0a43a">⚠ ' + esc(w) + "</span>" for w in k.get("warnings") or []]
            p.pack_start(card(label("\n".join(txt), markup=True)), False, False, 0)

    def render_backups(p):
        S = W.S
        pr = S.get("protection") or {}
        if not pr.get("asked"):
            box = vbox(6)
            box.pack_start(label("🛡 " + t("psTitle"), "sme-h2"), False, False, 0)
            box.pack_start(label(t("psHint"), "sme-muted"), False, False, 0)
            if S.get("baseline"):
                box.pack_start(label("✔ " + t("psListDone"), "sme-ok"), False, False, 0)
            inc = Gtk.CheckButton(label=t("psInc"))
            box.pack_start(inc, False, False, 0)
            r0 = Gtk.RadioButton.new_with_label(None, t("psNone"))
            r1 = Gtk.RadioButton.new_with_label_from_widget(r0, t("psFull"))
            r2 = Gtk.RadioButton.new_with_label_from_widget(r0, t("psLink"))
            if S.get("deployed"):
                box.pack_start(label(t("psDeployed"), "sme-muted"), False, False, 0)
            else:
                for r in (r0, r1, r2):
                    box.pack_start(r, False, False, 0)
            def apply():
                engine.update_settings(app.out, protection_asked=True, incremental_backup=inc.get_active())
                mode = "full" if r1.get_active() else "link" if r2.get_active() else ""
                if mode and not S.get("deployed"):
                    run("backup_" + mode, "")
                else:
                    refresh()
            box.pack_start(hbox(button(t("psApply"), apply, "suggested-action")), False, False, 0)
            p.pack_start(card(box), False, False, 0)
        page_header(p, t("gameBackups"), t("gbHint"))
        name = Gtk.Entry()
        name.set_placeholder_text(t("gbName"))
        row = Gtk.Box(spacing=8)
        row.pack_start(name, False, False, 0)
        for txt, act, cls in ((t("gbFull"), "backup_full", "suggested-action"), (t("gbLink"), "backup_link", None), (t("gbSaves"), "backup_full_saves", None)):
            row.pack_start(button(txt, lambda a=act: run(a, name.get_text().strip()), cls), False, False, 0)
        p.pack_start(row, False, False, 0)
        p.pack_start(label(t("gbLinkNote"), "sme-muted"), False, False, 0)
        for x in S.get("game_backups") or []:
            r = Gtk.Box(spacing=8)
            r.pack_start(label(f'<b>{esc(x["name"])}</b>\n<span foreground="#8a9ab5">{esc(str(x["game_version"]))} · {x["files"]} {esc(t("files"))} · {fmt_size(x["size"])} · {esc(x["mode"])}{" · saves" if x.get("saves") else ""} · {fmt_date(x["created"])}</span>', markup=True), True, True, 0)
            r.pack_end(button(t("del"), lambda n=x["name"]: confirm(t("gbDelConfirm").replace("%s", n)) and run("backup_delete", n), "destructive-action"), False, False, 0)
            r.pack_end(button(t("gbVerify"), lambda n=x["name"]: run("backup_verify", n)), False, False, 0)
            r.pack_end(button(t("gbRestore"), lambda n=x["name"]: confirm(t("gbConfirm").replace("%s", n)) and run("restore_game", n)), False, False, 0)
            p.pack_start(card(r), False, False, 0)
        if not S.get("game_backups"):
            p.pack_start(label(t("gbNone"), "sme-muted"), False, False, 0)
        page_header(p, t("incTitle"), t("incHint"))
        p.pack_start(hbox(label(f'{t("incState")}: {"✔" if pr.get("incremental") else "✖ " + t("prOff")} · {t("incStore")}: {pr.get("store_files", 0)} · {fmt_size(pr.get("store_size"))}'),
                          button(t("incOff"), lambda: run("incremental_off")) if pr.get("incremental") else button(t("incOn"), lambda: run("incremental_on"), "suggested-action")), False, False, 0)
        page_header(p, t("gcTitle"), t("gcHint"))
        bl = S.get("baseline")
        p.pack_start(label(f'{t("gcBase")}: {bl["version"]} · {bl["files"]} {t("files")} · {fmt_date(bl["created"])}' if bl else t("gcNoBase"), "sme-muted"), False, False, 0)
        p.pack_start(hbox(button(t("gcCheck"), lambda: run("game_check"), "suggested-action"),
                          button(t("gcRepair"), lambda: confirm(t("gcRepairConfirm")) and run("game_repair")),
                          button(t("gcBaseline"), lambda: run("game_baseline"))), False, False, 0)
        page_header(p, t("backupsPage"))
        b, o = S["backups"], S["overwrite"]
        p.pack_start(label(f'{t("backupFiles")}: {b["files"]} · {t("changedSets")}: {len(b["changed_sets"])}'), False, False, 0)
        p.pack_start(hbox(label(f'{t("overwrite")}: {o["total"]}'), button(t("openOw"), lambda: app.open_path("overwrite"))), False, False, 0)
        if o["files"]:
            p.pack_start(label("\n".join(o["files"][:200]), "sme-log"), False, False, 0)

    def render_settings(p):
        S = W.S
        page_header(p, t("settingsPage"))
        # profiles
        box = vbox(6)
        box.pack_start(label(t("profiles"), "sme-h2"), False, False, 0)
        d = S.get("deployed")
        for pf in S["profiles"]:
            r = Gtk.Box(spacing=8)
            r.pack_start(label(pf + ("  ✔" if pf == S["profile"] else ""), wrap=False), False, False, 0)
            if pf != S["profile"]:
                r.pack_start(button(t("switchTo"), lambda n=pf: switch_profile(n)), False, False, 0)
                if not (d and d["profile"] == pf):
                    r.pack_start(button(t("del"), lambda n=pf: confirm(t("confirmDel").replace("%s", n)) and run("profile_delete", n), "destructive-action"), False, False, 0)
            box.pack_start(r, False, False, 0)
        npf = Gtk.Entry()
        npf.set_placeholder_text(t("profileName"))
        box.pack_start(label(t("newProfile") + ":"), False, False, 0)
        box.pack_start(hbox(npf, button(t("create"), lambda: npf.get_text().strip() and run("profile_new", npf.get_text().strip()), "suggested-action")), False, False, 0)
        def from_coll(kind):
            nm = npf.get_text().strip()
            if not nm:
                return info(t("npNeed"))
            if kind == "file":
                dlg = Gtk.FileChooserDialog(title=t("npFile"), transient_for=win, action=Gtk.FileChooserAction.OPEN)
                dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL, Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
                ff = Gtk.FileFilter()
                ff.set_name("collection.json")
                ff.add_pattern("*.json")
                dlg.add_filter(ff)
                src = dlg.get_filename() if dlg.run() == Gtk.ResponseType.OK else None
                dlg.destroy()
            else:
                src = ask_text(t("npCollSrc"))
            if src:
                err = app.new_profile_from_collection(nm, src)
                if err:
                    info(t("busy") if err == "busy" else err)
                poll()
        box.pack_start(hbox(button(t("npFile"), lambda: from_coll("file")), button(t("npLink"), lambda: from_coll("link"))), False, False, 0)
        box.pack_start(label(t("npHint"), "sme-muted"), False, False, 0)
        p.pack_start(card(box), False, False, 0)
        # folders
        box = vbox(6)
        box.pack_start(label(t("paths"), "sme-h2"), False, False, 0)
        for k in ("archives", "mods"):
            e = Gtk.Entry()
            e.set_text(S["paths"][k])
            box.pack_start(label(t("pArchives" if k == "archives" else "pMods"), "sme-muted"), False, False, 0)
            box.pack_start(e, False, False, 0)
            box.pack_start(hbox(button(t("pSave"), lambda k=k, e=e: e.get_text().strip() and run("set_" + k, e.get_text().strip()), "suggested-action"),
                                button(t("pSaveMove"), lambda k=k, e=e: e.get_text().strip() and confirm(t("pMoveConfirm")) and run("set_" + k + "_move", e.get_text().strip())),
                                button(t("pDefault"), lambda k=k: run("set_" + k, "default")),
                                button("📂", lambda k=k: app.open_path(k))), False, False, 0)
        box.pack_start(label(t("pHint"), "sme-muted"), False, False, 0)
        p.pack_start(card(box), False, False, 0)
        # Nexus
        box = vbox(6)
        box.pack_start(label(t("nexus"), "sme-h2"), False, False, 0)
        nx = S.get("nexus")
        if nx:
            box.pack_start(label(f'{t("nxLogged")} {nx["name"]} ({t("nxPremium" if nx["premium"] else "nxFree")})'), False, False, 0)
            box.pack_start(hbox(button(t("nxLogout"), lambda: (engine.nexus_logout(), refresh()))), False, False, 0)
            if nx["premium"]:
                box.pack_start(hbox(button(t("nxDownload"), lambda: (lambda v: v and run("nexus_download", v))(ask_text(t("nxAsk"))))), False, False, 0)
        else:
            box.pack_start(label(t("nxNone") + " " + t("nxKeyHelp"), "sme-muted"), False, False, 0)
            def login():
                key = ask_text(t("nxKey"), password=True)
                if key:
                    if not engine.nexus_login(key):
                        info(t("nxLoginFail"))
                    refresh()
            box.pack_start(hbox(button(t("nxLogin"), login, "suggested-action")), False, False, 0)
        box.pack_start(hbox(button(t("nxHandler"), lambda: run("nxm_handler"))), False, False, 0)
        box.pack_start(label(t("nxHandlerHelp"), "sme-muted"), False, False, 0)
        p.pack_start(card(box), False, False, 0)
        # language
        box = vbox(6)
        box.pack_start(label(t("language"), "sme-h2"), False, False, 0)
        cur_lang = S.get("lang") or "uk"
        lang = choice([("uk", "Українська"), ("en", "English")], cur_lang, lambda i: None if i == cur_lang else (app.set_lang(i), refresh()))
        box.pack_start(hbox(lang), False, False, 0)
        p.pack_start(card(box), False, False, 0)

    RENDER = {"fomod": render_fomod, "collection": render_collection, "plugins": render_plugins, "deps": render_deps,
              "conflicts": render_conflicts, "deploy": render_deploy, "backups": render_backups, "settings": render_settings}

    def render_page(name):
        if name == "mods":
            return
        p = pages[name]
        W.building = True
        clear(p)
        p.set_border_width(4)
        RENDER[name](p)
        p.show_all()
        W.building = False

    def titles():
        S = W.S
        counts = {"mods": len(S["mods"]), "fomod": len(S["fomods"]), "conflicts": S["conflicts"]["pairs_total"] or "",
                  "deps": len(S["problems"]) or "", "plugins": len(S["deployed"]["plugins"]) if S.get("deployed") else "",
                  "collection": sum(1 for m in (S.get("collection") or {}).get("mods", []) if not m["have"] and not m["optional"]) or "",
                  "backups": "!" if not (S.get("protection") or {}).get("asked") or not S.get("baseline") else ""}
        for k in PAGE_KEYS:
            child = stack.get_child_by_name(k)
            n = counts.get(k, "")
            stack.child_set_property(child, "title", t("home" if k == "mods" else k) + (f"  ({n})" if n != "" else ""))
        add_btn.set_label("＋ " + t("addMods"))
        items = [(t("openArchives"), lambda: app.open_path("archives")), (t("extract"), lambda: run("scan"))]
        if S.get("nexus") and S["nexus"]["premium"]:
            items.append((t("nxDownload"), lambda: (lambda v: v and run("nexus_download", v))(ask_text(t("nxAsk")))))
        add_btn.set_popover(popover_menu(add_btn, items))
        extract_btn.set_label("⤓ " + t("extract"))
        fomod_btn.set_label("⬢ " + t("installFomod"))
        search.set_placeholder_text(t("search"))
        for k in ("#", "colName", "colCat", "colVer", "colStatus"):
            cols[k].set_title("#" if k == "#" else t(k))
        loglabel.set_text(t("log"))
        win.set_title(f"Skyrim Mod Engine v{S.get('version', '?')} — {S['profile']}")

    def refresh():
        try:
            W.S = app.state()
        except Exception:
            import traceback
            append_log("ERROR (state)\n" + traceback.format_exc())
            return
        if W.sel and not any(m["name"] == W.sel for m in W.S["mods"]):
            W.sel = None
        if not W.sel and W.S["mods"]:
            W.sel = W.S["mods"][0]["name"]
        titles()
        fill_mods()
        render_mod_banners()
        render_page(stack.get_visible_child_name())
        render_right()
        if isinstance(banner, Gtk.Label):
            banner.set_markup(f'<b>Skyrim Mod Engine</b>  <span foreground="#8a9ab5" size="small">v{esc(str(W.S.get("version", "?")))} · {esc(t("tagline"))}</span>')
        else:
            banner.queue_draw()
        job_line()

    def on_page(*_):
        if W.S:
            render_page(stack.get_visible_child_name())
            render_right()
    stack.connect("notify::visible-child-name", on_page)

    def append_log(text):
        buf = logview.get_buffer()
        buf.insert(buf.get_end_iter(), ("\n" if buf.get_char_count() else "") + text)
        if buf.get_char_count() > 300000:
            buf.delete(buf.get_start_iter(), buf.get_iter_at_offset(buf.get_char_count() - 200000))
        logview.scroll_to_mark(buf.get_insert(), 0, False, 0, 0)
        buf.place_cursor(buf.get_end_iter())

    def job_line():
        j = app.job
        running = bool(j and j.get("running"))
        (spinner.start if running else spinner.stop)()
        jobl.set_text((t("working") + " " + " ".join(j["args"])) if running else (f"rc={j['rc']}" if j and j.get("rc") else t("idle")))

    def poll():
        r = app.log_since(W.log_from)
        if r["lines"]:
            append_log("\n".join(r["lines"]))
        W.log_from = r["next"]
        running = bool(r["job"] and r["job"].get("running"))
        if W.was_running and not running:
            refresh()
        elif running != W.was_running:
            render_right()
        W.was_running = running
        job_line()
        return True

    def on_close(*_):
        if app.job and app.job.get("running"):
            info(t("busy"))
            return True  # never leave a deployment half-done by closing the window
        Gtk.main_quit()
        return False
    win.connect("delete-event", on_close)

    GLib.timeout_add(400, poll)
    win.show_all()
    win.maximize()
    refresh()
    if not engine.load_baseline(app.out) and engine.find_game_dir(None):
        GUI_ACTIONS["baseline_auto"] = ["--game-baseline"]
        app.start("baseline_auto")
    if on_ready:
        on_ready({"win": win, "app": app, "stack": stack, "tree": tree, "refresh": refresh, "run": run, "W": W,
                  "pages": pages, "right": right, "GLib": GLib, "Gtk": Gtk})
    Gtk.main()
    return 0


def ask(prompt: str) -> str:
    """input() that does not crash without a terminal (shortcut, background run): EOF = default answer."""
    try:
        return input(prompt).strip().lower()
    except EOFError:
        print(tr("(немає вводу — беру відповідь за замовчуванням)"))
        return ""

def configured_dirs(base: Path, output_root: Path, archives: str | None = None, mods: str | None = None) -> tuple[Path, Path]:
    """Archives and mods folders: this run's option, else the saved choice, else next to the script."""
    st = read_settings(output_root)
    a = archives or st.get("archives_dir") or "mods-archives"
    m = mods or st.get("mods_dir") or "Mods"
    return (base / Path(a).expanduser()).resolve(), (base / Path(m).expanduser()).resolve()

def set_folder(kind: str, value: str, base: Path, output_root: Path, move: bool, game_arg: str | None) -> int:
    key = "archives_dir" if kind == "archives" else "mods_dir"
    old_a, old_m = configured_dirs(base, output_root)
    old = old_a if kind == "archives" else old_m
    new = (base / ("mods-archives" if kind == "archives" else "Mods")).resolve() if value.strip().lower() == "default" \
        else (base / Path(value).expanduser()).resolve()
    label = tr("архівів") if kind == "archives" else tr("модів")
    game = find_game_dir(game_arg)
    if game and (new == game.resolve() or ci_join(game, "Data") and (new == ci_join(game, "Data").resolve() or ci_join(game, "Data").resolve() in new.parents)):
        print(tr("Папка {0} не може бути папкою гри чи всередині Data: {1}", label, new))
        return 1
    if new == old:
        print(tr("Папка {0} вже {1}", label, new))
        return 0
    if new in old.parents or old in new.parents:
        print(tr("Нова папка {0} не може бути всередині старої чи навпаки: {1}", label, new))
        return 1
    if kind == "mods" and move and load_journal(output_root):
        print(tr("Моди зараз розгорнуто — спершу --undeploy, потім перенось папку модів."))
        return 1
    new.mkdir(parents=True, exist_ok=True)
    if move and old.is_dir() and any(old.iterdir()):
        if any(new.iterdir()):
            print(tr("Нова папка {0} не порожня — переносити не буду: {1}", label, new))
            return 1
        print(tr("Переношу вміст {0} → {1}...", old, new), flush=True)
        for p in sorted(old.iterdir()):
            shutil.move(str(p), new / p.name)  # a rename on the same disk, copy + delete across disks
        if kind == "mods":
            sp = output_root / "processed_archives.json"
            if sp.exists():
                st = json.loads(sp.read_text(encoding="utf-8"))
                for rec in st.values():
                    d = rec.get("destination")
                    if d and Path(d).parent == old:
                        rec["destination"] = str(new / Path(d).name)
                sp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    elif old.is_dir() and any(old.iterdir()) and not any(new.iterdir()):
        print(tr("У старій папці {0} є файли ({1}); їх не переношу. Перенести: додай --move.", label, old))
    if value.strip().lower() == "default":
        data = read_settings(output_root)
        data.pop(key, None)
        output_root.mkdir(parents=True, exist_ok=True)
        (output_root / SETTINGS_FILE).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    else:
        update_settings(output_root, **{key: str(new)})
    print(tr("Папка {0}: {1}", label, new))
    if kind == "mods" and game:
        try:
            if os.stat(new).st_dev != os.stat(game).st_dev:
                print(tr("УВАГА: папка модів на іншому диску, ніж гра — розгортання копіюватиме файли замість жорстких посилань (займе місце на диску гри). Краще тримати моди на тому ж диску, що й гру."))
        except OSError:
            pass
    return 0

def main() -> int:
    parser = argparse.ArgumentParser(description=tr("Skyrim Mod Engine v0.10 — аналіз, розпакування, каталог модів, перевірка SKSE."))
    parser.add_argument("--archives", help=tr("Папка з архівами на цей запуск (за замовч. збережена через --set-archives, інакше ./mods-archives)"))
    parser.add_argument("--mods", help=tr("Папка з розпакованими модами на цей запуск (за замовч. збережена через --set-mods, інакше ./Mods)"))
    parser.add_argument("--set-archives", metavar="PATH", help=tr("Запам'ятати свою папку з архівами модів ('default' — назад до ./mods-archives)."))
    parser.add_argument("--set-mods", metavar="PATH", help=tr("Запам'ятати свою папку для розпакованих модів ('default' — назад до ./Mods)."))
    parser.add_argument("--move", action="store_true", help=tr("З --set-archives/--set-mods: перенести вміст старої папки в нову."))
    parser.add_argument("--output", default="mod_engine_output", help=tr("Папка для звітів (за замовч. ./mod_engine_output)"))
    parser.add_argument("--scan-only", action="store_true", help=tr("Лише проаналізувати нові архіви, не розпаковувати."))
    parser.add_argument("--yes", action="store_true", help=tr("Розпаковувати нові архіви без питань."))
    parser.add_argument("--skse", action="store_true", help=tr("Перевірити SKSE, Address Library і SKSE-плагіни модів."))
    parser.add_argument("--game", help=tr("Папка гри (за замовч. шукається в бібліотеках Steam)."))
    parser.add_argument("--fomod", action="store_true", help=tr("Встановити FOMOD-моди (вибір варіантів) у папки «<мод> [FOMOD]»."))
    parser.add_argument("--fomod-redo", action="store_true", help=tr("Перевстановити вже встановлені FOMOD (з урахуванням збереженого вибору)."))
    parser.add_argument("--auto", action="store_true", help=tr("FOMOD без питань: вибір за залежностями, SKSE і рекомендаціями автора."))
    parser.add_argument("--conflicts", action="store_true", help=tr("Показати конфлікти файлів між модами з урахуванням порядку (modlist.txt)."))
    parser.add_argument("--deploy", action="store_true", help=tr("Розгорнути моди в папку гри (жорсткі посилання, з бекапом) і оновити plugins.txt."))
    parser.add_argument("--undeploy", action="store_true", help=tr("Повністю відкотити розгортання: прибрати файли модів, відновити оригінали і plugins.txt."))
    parser.add_argument("--skse-launcher", action="store_true", help=tr("Кнопка «Грати» в Steam запускатиме SKSE (підміна SkyrimSELauncher.exe, з бекапом)."))
    parser.add_argument("--skse-launcher-restore", action="store_true", help=tr("Повернути оригінальний SkyrimSELauncher.exe."))
    parser.add_argument("--collection", help=tr("collection.json колекції Vortex/Nexus: вибори FOMOD автора, порядок модів і плагінів, перевірка архівів. 'none' — відключити."))
    parser.add_argument("--no-loot", action="store_true", help=tr("Не використовувати masterlist LOOT для порядку плагінів."))
    parser.add_argument("--loot-update", action="store_true", help=tr("Примусово оновити masterlist LOOT (інакше раз на 7 днів)."))
    parser.add_argument("--dry-run", action="store_true", help=tr("З --deploy: показати план без змін."))
    parser.add_argument("--skse-log", help=tr("Шлях до skse64.log (за замовч. шукається в compatdata Proton)."))
    parser.add_argument("--profile", help=tr("Профіль, з яким працювати (запам'ятовується). За замовч. — останній вибраний, спершу Default."))
    parser.add_argument("--profile-new", metavar="NAME", help=tr("Створити новий профіль і перемкнутися на нього: усі моди вимкнені, а з --collection/--collection-nexus увімкнені моди колекції."))
    parser.add_argument("--profile-delete", metavar="NAME", help=tr("Видалити профіль (не активний і не розгорнутий)."))
    parser.add_argument("--profiles", action="store_true", help=tr("Показати список профілів."))
    parser.add_argument("--nexus-login", nargs="?", const="", metavar="KEY", help=tr("Увійти в Nexus Mods персональним API-ключем (без KEY — запитає)."))
    parser.add_argument("--nexus-logout", action="store_true", help=tr("Видалити збережений ключ Nexus."))
    parser.add_argument("--nexus-status", action="store_true", help=tr("Показати, чи виконано вхід у Nexus."))
    parser.add_argument("--nexus-download", metavar="MOD[:FILE]", help=tr("Завантажити файл мода з Nexus (лише Premium): ID мода, ID:ID_файлу або посилання на сторінку."))
    parser.add_argument("--nxm", metavar="URL", help=tr("Обробити посилання nxm:// («Mod Manager Download» на сайті)."))
    parser.add_argument("--nxm-handler", action="store_true", help=tr("Зареєструвати скрипт як обробник посилань nxm:// у системі."))
    parser.add_argument("--collection-nexus", metavar="LINK", help=tr("Взяти колекцію прямо з Nexus: посилання на сторінку колекції, nxm:// або її код."))
    parser.add_argument("--skse-install", action="store_true", help=tr("Завантажити з Nexus SKSE, що підходить до версії гри."))
    parser.add_argument("--collection-download", action="store_true", help=tr("Завантажити відсутні архіви колекції з Nexus (лише Premium)."))
    parser.add_argument("--with-optional", action="store_true", help=tr("З --collection-download: також необов'язкові моди колекції."))
    parser.add_argument("--backup-game", nargs="?", const="", metavar="NAME", help=tr("Зберегти бекап чистої гри (папка гри + INI і plugins.txt з prefix). Без NAME — версія й дата."))
    parser.add_argument("--backup-mode", choices=("full", "link"), default="full", help=tr("full — повні копії (безпечно, потрібне місце); link — жорсткі посилання (без місця, не захищає від змін файлів на місці)."))
    parser.add_argument("--with-saves", action="store_true", help=tr("З --backup-game: також скопіювати сейви."))
    parser.add_argument("--restore-game", metavar="NAME", help=tr("Відновити гру з бекапу (моди спершу прибираються; замінене відкладається, не видаляється)."))
    parser.add_argument("--backups", action="store_true", help=tr("Показати бекапи гри."))
    parser.add_argument("--backup-verify", metavar="NAME", help=tr("Перевірити цілісність бекапу (SHA-1 кожного файлу)."))
    parser.add_argument("--incremental-backup", choices=("on", "off"), help=tr("Під час розгортання зберігати копію кожного оригінального файлу гри, який перекривають моди (за замовч. увімкнено; запам'ятовується)."))
    parser.add_argument("--game-check", action="store_true", help=tr("Порівняти гру зі списком її файлів (знайти змінене, відсутнє, зайве)."))
    parser.add_argument("--game-repair", action="store_true", help=tr("Прибрати зайве з гри й повернути змінені файли з бекапів (решту — перевіркою Steam)."))
    parser.add_argument("--game-baseline", action="store_true", help=tr("Заново зберегти список файлів гри з SHA-1 (напр., після оновлення Steam)."))
    parser.add_argument("--backup-delete", metavar="NAME", help=tr("Видалити бекап гри."))
    parser.add_argument("--gui", nargs="?", const="auto", choices=("auto", "gtk", "web"), help=tr("Графічний інтерфейс: вікно GTK, якщо є, інакше сторінка в браузері (також: подвійний клік по скрипту). gtk/web — примусово."))
    parser.add_argument("--lang", choices=LANGS, help="Interface language / мова інтерфейсу: en, uk (remembered / запам'ятовується).")
    args = parser.parse_args()
    if args.lang:
        save_lang(args.lang)
    # Double-click in a file manager: no arguments and no terminal -> graphical interface.
    if args.gui or (len(sys.argv) == 1 and not sys.stdin.isatty()):
        mode = args.gui or "auto"
        if mode in ("auto", "gtk") and gtk_available() and (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
            return gtk_run(sys.modules[__name__], Path(__file__).resolve(), args.output)
        if mode == "gtk":
            print(tr("GTK 3 (PyGObject) недоступний — відкриваю інтерфейс у браузері."))
        return gui_run(sys.modules[__name__], Path(__file__).resolve(), args.output)

    if args.nxm:  # started by the browser: work in the script's own folder
        os.chdir(Path(__file__).resolve().parent)
    cwd = Path.cwd()
    output_root = (cwd / args.output).resolve()
    if args.set_archives or args.set_mods:
        rc = 0
        for kind, val in (("archives", args.set_archives), ("mods", args.set_mods)):
            if val:
                rc = set_folder(kind, val, cwd, output_root, args.move, args.game) or rc
        if not (args.yes or args.scan_only or args.fomod or args.fomod_redo or args.deploy or args.conflicts or args.skse):
            return rc
    archives_root, mods_root = configured_dirs(cwd, output_root, args.archives, args.mods)
    for d in (archives_root, mods_root, output_root):
        d.mkdir(parents=True, exist_ok=True)
    migrate_to_profiles(output_root)
    profile = read_settings(output_root).get("profile") or DEFAULT_PROFILE
    if profile not in list_profiles(output_root):
        profile = DEFAULT_PROFILE
        profile_dir(output_root, profile).mkdir(parents=True, exist_ok=True)
    if args.profile:
        match = [p for p in list_profiles(output_root) if p.lower() == args.profile.lower()]
        if not match:
            print(tr("Профілю «{0}» немає. Є: {1}. Створити: --profile-new \"{0}\"", args.profile, ", ".join(list_profiles(output_root))))
            return 1
        profile = match[0]
    migrate_collection(output_root)
    if args.profile_new:
        if not create_profile(output_root, profile, args.profile_new,
                              "collection" if (args.collection and args.collection.lower() != "none") or args.collection_nexus else "empty"):
            return 1
        profile = args.profile_new
    if args.profile or args.profile_new:
        update_settings(output_root, profile=profile)
    if args.profile_delete:
        delete_profile(output_root, mods_root, args.profile_delete, profile)
    pdir = profile_dir(output_root, profile)
    modlist_path = pdir / "modlist.txt"

    if args.backup_game is not None:
        update_settings(output_root, protection_asked=True)
    if args.incremental_backup:
        update_settings(output_root, incremental_backup=args.incremental_backup == "on", protection_asked=True)
        n_all, b_all = store_stats(output_root)
        print(tr("Інкрементальний бекап при розгортанні: {0}. У сховищі {1} файл(ів), {2}.",
                 tr("увімкнено") if args.incremental_backup == "on" else tr("вимкнено"), n_all, human_size(b_all)))
    # Game backups (clean game snapshot / restore) and the first-run file list of the game
    excl = [archives_root, mods_root, output_root, Path(__file__).resolve(), Path(__file__).resolve().parent / "__pycache__"]
    # The file list of the game is made automatically (no disk space to speak of); everything that
    # copies game files is the user's decision.
    if not args.nxm and not args.game_baseline:
        gd0 = find_game_dir(args.game)
        if gd0 and not game_running():
            if not (output_root / GAME_BASELINE).exists():
                print(tr("\n=== Перший запуск: список файлів гри ==="))
                make_baseline(gd0, output_root, excl)
            if not read_settings(output_root).get("protection_asked") and not (args.incremental_backup or args.backup_game is not None):
                if sys.stdin.isatty() and not args.yes:
                    first_run_protection(gd0, output_root, excl)
                elif not os.environ.get("SME_GUI"):  # the GUI shows its own setup panel
                    protection_notice()
    if args.game_baseline or args.game_check or args.game_repair:
        gd = find_game_dir(args.game)
        if not gd:
            print(tr('\nПапку гри не знайдено. Вкажи її через --game "/шлях/до/Skyrim Special Edition"'))
            return 1
        if args.game_baseline:
            make_baseline(gd, output_root, excl)
        if args.game_repair:
            return game_repair(gd, output_root, excl)
        if args.game_check:
            r = game_check(gd, output_root, excl)
            if r:
                print_game_check(r)
            return 0 if r and not (r["changed"] or r["missing"] or r["extra"]) else 1
        if args.backup_game is None:
            return 0
    if args.backup_game is not None or args.restore_game or args.backups or args.backup_verify or args.backup_delete:
        gd = find_game_dir(args.game)
        rc = 0
        if args.backup_delete:
            rc = delete_backup(output_root, args.backup_delete) or rc
        if args.backup_verify:
            bad = verify_backup(output_root, args.backup_verify)
            print(tr("Бекап «{0}» цілий.", args.backup_verify) if not bad else
                  tr("Бекап «{0}» пошкоджений: {1} файл(ів) не збігаються, напр. {2}", args.backup_verify, len(bad), ", ".join(bad[:5])))
            rc = rc or (1 if bad else 0)
        if (args.backup_game is not None or args.restore_game) and not gd:
            print(tr('\nПапку гри не знайдено. Вкажи її через --game "/шлях/до/Skyrim Special Edition"'))
            return 1
        if args.backup_game is not None:
            rc = backup_game(gd, output_root, excl, args.backup_game or None, args.backup_mode, args.with_saves) or rc
        if args.restore_game:
            rc = restore_game(gd, output_root, excl, args.restore_game) or rc
        if args.backups:
            print_backups(output_root)
        return rc

    # Nexus Mods account and downloads (before the archive scan, so new archives are picked up below)
    if args.nxm:
        return handle_nxm(args.nxm, archives_root, output_root)
    if args.nexus_logout:
        nexus_logout()
    if args.nexus_login is not None:
        nexus_login(args.nexus_login or None)
    if args.nexus_status:
        nexus_status()
    if args.nxm_handler:
        nxm_handler_install(Path(__file__).resolve())
    nexus_rc = 0
    if args.nexus_download:
        acc = nexus_account()
        m = re.search(r"mods/(\d+)(?:.*?file_id=(\d+))?", args.nexus_download) or \
            re.fullmatch(r"\s*(\d+)(?:\s*:\s*(\d+))?\s*", args.nexus_download)
        if not acc:
            print(tr("Спершу увійди в Nexus: --nexus-login"))
            nexus_rc = 1
        elif not m:
            print(tr("Не розібрав «{0}»: потрібен ID мода, ID:ID_файлу або посилання на сторінку мода", args.nexus_download))
            nexus_rc = 1
        else:
            try:
                mod_id = int(m.group(1))
                file_id = int(m.group(2)) if m.group(2) else nexus_pick_file(mod_id, acc["api_key"])
                nexus_download(mod_id, file_id, archives_root, acc["api_key"])
            except NexusError as ex:
                print(tr("ПОМИЛКА: {0}", ex))
                if not acc.get("is_premium"):
                    print(tr("Без Premium завантажуй кнопкою «Mod Manager Download» на сайті (потрібен --nxm-handler)."))
                nexus_rc = 1
    if args.collection_nexus:
        if not fetch_collection(args.collection_nexus, output_root, pdir):
            nexus_rc = 1
    if args.skse_install:
        gd = find_game_dir(args.game)
        if not gd:
            print(tr('\nПапку гри не знайдено. Вкажи її через --game "/шлях/до/Skyrim Special Edition"'))
            nexus_rc = 1
        else:
            nexus_rc = skse_install(gd, mods_root, archives_root) or nexus_rc
    if args.collection_download:
        c = load_collection(pdir, args.collection)
        if not c:
            print(tr("Колекцію не задано: --collection шлях/до/collection.json"))
            nexus_rc = 1
        else:
            nexus_rc = collection_download(c, archives_root, output_root, args.with_optional) or nexus_rc
    if (args.nexus_logout or args.nexus_login is not None or args.nexus_status or args.nxm_handler) \
            and not (args.nexus_download or args.collection_download):
        return 0
    index_dir = output_root / "index"
    state_path = output_root / "processed_archives.json"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    except Exception:
        state = {}

    tools = find_tools()
    all_archives = sorted(p for p in archives_root.rglob("*") if p.is_file() and is_archive(p))
    archive_paths = [p for p in all_archives if not SECONDARY_VOLUME_RE.search(p.name)]
    report = {
        "engine_version": "0.10",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "archives_directory": str(archives_root),
        "mods_directory": str(mods_root),
        "archive_tools": sorted(tools),
        "archives": [],
        "mods": [],
    }

    print("Skyrim Mod Engine v0.10")
    print(tr("Архіви: {0}", archives_root))
    print(tr("Моди: {0}", mods_root))
    print(tr("Профіль: {0}", profile))
    _j = load_journal(output_root)
    if _j and _j.get("profile", DEFAULT_PROFILE) != profile and not (args.deploy or args.undeploy):
        print(tr("  (у грі зараз розгорнуто профіль «{0}»; щоб перемкнути гру — --deploy)", _j.get("profile", DEFAULT_PROFILE)))
    print(tr("Інструменти для 7z/RAR/TAR: {0}", ', '.join(tools) or tr('не знайдено')))
    print(tr("Знайдено архівів: {0}\n", len(archive_paths)))

    skipped_known = 0
    new_this_run: set[str] = set()  # mods extracted now: enabled in the active profile
    for archive in archive_paths:
        rel = str(archive.relative_to(archives_root))
        st = archive.stat()
        prev = state.get(rel, {})
        # Re-hash only when size or mtime changed: hashing gigabytes on every run is slow on the Deck.
        if prev.get("size") == st.st_size and prev.get("mtime_ns") == st.st_mtime_ns and prev.get("sha256"):
            digest = prev["sha256"]
        else:
            digest = sha256_file(archive)
        rec = {"archive": rel, "size": human_size(st.st_size), "sha256": digest}

        if prev.get("sha256") == digest and prev.get("destination"):
            # The mod folder was deleted by hand: forget it and extract again below.
            new_name = clean_mod_name(Path(FIRST_VOLUME_RE.sub(r"\1", archive.name)))
            if not Path(prev["destination"]).is_dir() and not (mods_root / new_name).is_dir():
                print(tr("Папку «{0}» видалено — розпакую {1} знову", Path(prev['destination']).name, archive.name))
                state.pop(rel, None)
                prev = {}
        if prev.get("sha256") == digest and prev.get("destination"):
            # Folder named by an older, weaker name-cleaning rule: rename it if the better name is free.
            old_dest = Path(prev["destination"])
            new_dest = mods_root / clean_mod_name(Path(FIRST_VOLUME_RE.sub(r"\1", archive.name)))
            if old_dest.is_dir() and old_dest.parent == mods_root and old_dest.name != new_dest.name \
                    and not new_dest.exists() and not args.scan_only:
                old_dest.rename(new_dest)
                prev["destination"] = str(new_dest)
                print(tr("Перейменовано: '{0}' -> '{1}'", old_dest.name, new_dest.name))
            rec.update(status="installed", mod=Path(prev["destination"]).name)
            report["archives"].append(rec)
            prev.update(size=st.st_size, mtime_ns=st.st_mtime_ns)
            skipped_known += 1
            continue

        tool = choose_tool(archive, tools)
        is_zip = zipfile.is_zipfile(archive)
        rec["tool"] = "python-zipfile" if is_zip else (Path(tool).name if tool else None)
        mod_name = clean_mod_name(Path(FIRST_VOLUME_RE.sub(r"\1", archive.name)))
        try:
            if is_zip:
                inv = zip_inventory(archive)
            elif tool:
                inv = external_inventory(archive, tool)
            else:
                inv = {"readable": False, "error": tr("Для цього формату потрібна утиліта 7z, bsdtar, unar або unrar.")}
        except Exception as e:
            inv = {"readable": False, "error": str(e)}

        print(f"• {archive.name} ({rec['size']})")
        print(tr("  Назва папки: {0}", mod_name))
        if not inv.get("readable"):
            err = str(inv.get("error", tr("невідома помилка"))).strip()[-400:]
            print(tr("  Не вдалося прочитати вміст: {0}\n", err))
            rec.update(status="unreadable", error=err)
            report["archives"].append(rec)
            continue
        print(tr("  Вміст: {0} файлів; плагіни: {1}; верхні папки: {2}", inv['file_count'], len(inv['plugins']), ', '.join(inv['top_level'][:8]) or tr('(немає)')))
        for plugin in inv["plugins"][:20]:
            print(f"    ESP/ESM/ESL: {plugin}")
        if len(inv["plugins"]) > 20:
            print(tr("    ...і ще {0}", len(inv['plugins']) - 20))
        rec.update(status="new", file_count=inv["file_count"])

        if args.scan_only:
            print()
            report["archives"].append(rec)
            continue

        destination = mods_root / mod_name
        if destination.exists():
            print(tr("  Папка вже існує: {0}", destination.name))
            if args.yes:
                print(tr("  --yes задано, але наявну папку не перезаписую; архів пропущено.\n"))
                rec["status"] = "skipped-name-collision"
                report["archives"].append(rec)
                continue
            answer = ask(tr("  [o] розпакувати в нову папку з суфіксом / [s] пропустити? [s]: "))
            if answer != "o":
                print(tr("  Пропущено.\n"))
                rec["status"] = "skipped"
                report["archives"].append(rec)
                continue
            n = 2
            while (mods_root / f"{mod_name} ({n})").exists():
                n += 1
            destination = mods_root / f"{mod_name} ({n})"

        if not args.yes:
            answer = ask(tr("  Розпакувати в '{0}'? [Y/n]: ", destination.name))
            if answer not in ("", "y", "yes", "т", "так"):
                print(tr("  Пропущено.\n"))
                rec["status"] = "skipped"
                report["archives"].append(rec)
                continue

        staging = mods_root / f"{STAGING_PREFIX}{destination.name}"
        if staging.exists():
            shutil.rmtree(staging)
        try:
            if is_zip:
                try:
                    count, warnings = extract_zip(archive, staging)
                except NotImplementedError:
                    # Python's zipfile lacks some methods (Deflate64, PPMd...); 7z/bsdtar handle them
                    zip_tool = choose_tool(archive, tools)
                    if not zip_tool:
                        raise RuntimeError(tr("ZIP із методом стиснення, який Python не підтримує, а 7z/bsdtar немає"))
                    shutil.rmtree(staging, ignore_errors=True)
                    count, warnings = extract_external(archive, staging, zip_tool)
                    warnings = warnings + [tr("розпаковано через {0} (нестандартне стиснення ZIP)", Path(zip_tool).name)]
            else:
                if not tool:
                    raise RuntimeError(tr("Немає утиліти для цього формату."))
                count, warnings = extract_external(archive, staging, tool)
            links = [str(p.relative_to(staging)) for p in staging.rglob("*") if p.is_symlink()]
            if links:
                raise RuntimeError(tr("Архів містить символьні посилання, моди їх не потребують: ") + ", ".join(links[:5]))
            staging.rename(destination)
            new_this_run.add(destination.name)
            rec.update(status="installed", mod=destination.name)
            if warnings:
                rec["warnings"] = warnings[:10]
            state[rel] = {"sha256": digest, "size": st.st_size, "mtime_ns": st.st_mtime_ns,
                          "destination": str(destination),
                          "processed_at": datetime.now().astimezone().isoformat(timespec="seconds")}
            print(tr("  Готово: розпаковано файлів: {0} -> {1}\n", count, destination))
        except Exception as e:
            shutil.rmtree(staging, ignore_errors=True)
            rec.update(status="error", error=str(e).strip()[-400:])
            print(tr("  ПОМИЛКА розпакування: {0}\n", str(e).strip()[-600:]))
        report["archives"].append(rec)

    if skipped_known:
        print(tr("Уже розпаковані раніше (без змін): {0}\n", skipped_known))

    # Catalog extracted mods: layout + compact summary; full file lists go to index/*.tsv.gz.
    live_index = set()
    layouts: dict[str, int] = {}
    children = sorted(mods_root.iterdir(), key=lambda p: p.name.lower())
    for n_child, child in enumerate(children, 1):
        if len(children) > 150 and n_child % 100 == 0:
            print(tr("  каталог: {0}/{1} модів...", n_child, len(children)), flush=True)
        if not child.is_dir() or child.name.startswith(STAGING_PREFIX):
            continue
        s = summarize_mod(child, index_dir)
        live_index.add(s["index"])
        layouts[s["layout"]] = layouts.get(s["layout"], 0) + 1
        report["mods"].append(s)

    coll = load_collection(pdir, args.collection)
    idx_to_mod: dict[int, str] = {}
    if coll:
        cmatch = match_collection(coll, archives_root, output_root)
        idx_to_mod = collection_mod_names(coll, cmatch, state)
        gd = find_game_dir(args.game)
        gexe = ci_join(gd, "SkyrimSE.exe") if gd else None
        gv = pe_file_version(gexe) if gexe else None
        collection_report(coll, cmatch, archives_root, output_root, ".".join(map(str, gv)) if gv else None)
    fomod_new: set[str] = set()
    if args.fomod or args.fomod_redo:
        interactive = sys.stdin.isatty() and not args.auto and not args.yes
        new_dirs, renames = install_fomods(mods_root, output_root, report["mods"], find_game_dir(args.game),
                                           interactive, args.fomod_redo,
                                           collection_fomod_choices(coll, idx_to_mod) if coll else None,
                                           profile, pdir)
        fomod_new = set(new_dirs)
        existing = {p.name for p in mods_root.iterdir() if p.is_dir()}
        report["mods"] = [m for m in report["mods"] if m["name"] in existing]
        for name in new_dirs:
            s = summarize_mod(mods_root / name, index_dir)
            live_index.add(s["index"])
            report["mods"] = [m for m in report["mods"] if m["name"] != name] + [s]
        if renames and modlist_path.exists():
            # A new FOMOD variant takes the place of the old folder in this profile's mod order.
            lines = modlist_path.read_text(encoding="utf-8").splitlines()
            for i, l in enumerate(lines):
                t = l.strip()
                if t in renames:
                    lines[i] = renames[t]
                elif t.startswith("-") and t[1:].strip() in renames:
                    lines[i] = "-" + renames[t[1:].strip()]
            modlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        report["mods"].sort(key=lambda m: m["name"].lower())
        layouts = {}
        for m in report["mods"]:
            layouts[m["layout"]] = layouts.get(m["layout"], 0) + 1
        print()
    names = {m["name"] for m in report["mods"]}
    owned = fomod_owned(pdir, {m["name"] for m in report["mods"] if m["layout"] == "fomod"}, names)
    for m in report["mods"]:
        m.pop("installed_as", None)
        if m["layout"] == "fomod" and m["name"] in owned:
            m["installed_as"] = owned[m["name"]]
    view = profile_view(report["mods"], owned)
    for stale in index_dir.glob("*.tsv.gz"):
        if stale.name not in live_index:
            stale.unlink()
    print(tr("Моди в каталозі: {0}", len(report['mods'])))
    for lay, n in sorted(layouts.items()):
        print(f"  {lay}: {n}")
    for s in view:
        if s["layout"] not in ("data", "root+data", "root-only") and not s.get("installed_as"):
            extra = tr(" — варіанти: {0}", ', '.join(s.get('options', [])[:6])) if s.get("options") else ""
            print(f"  ! {s['name']}: {s['layout']}{extra}")
            if s["layout"] == "fomod":
                print(tr("    -> запусти з --fomod, щоб вибрати варіант (або --fomod --auto)"))

    # Mod order is kept up to date on every run; conflicts are computed on request.
    dep_mods = deployable(view)
    deps = mod_dependencies(mods_root, dep_mods)
    order, order_notes = sync_modlist(modlist_path, dep_mods, deps, new_this_run | fomod_new)
    disabled = read_disabled(modlist_path, {m["name"] for m in dep_mods})
    init = take_profile_init(pdir)
    if init:
        enabled: set[str] = set()
        if init == "collection" and coll:
            in_coll = set(idx_to_mod.values())
            for m in dep_mods:
                fm = FOMOD_OUT_RE.match(m["name"])
                if m["name"] in in_coll or (fm and fm.group(1) in in_coll):
                    enabled.add(m["name"])
        disabled = {m["name"] for m in dep_mods} - enabled
        write_modlist(modlist_path, order, disabled)
        if enabled:
            print(tr("Профіль «{0}»: увімкнено модів колекції {1}, решту ({2}) вимкнено.", profile, len(enabled), len(disabled)))
            if collection_fomod_choices(coll, idx_to_mod):
                print(tr("  У колекції є вибори FOMOD — застосуй їх для цього профілю: --fomod-redo --auto"))
        else:
            print(tr("Профіль «{0}»: усі моди вимкнено — увімкни потрібні (прибери «-» у modlist.txt або в GUI).", profile))
    if coll:
        order, cnotes = apply_collection_mod_rules(coll, idx_to_mod, order, deps)
        write_modlist(modlist_path, order, disabled)
        order_notes += cnotes
        if not args.conflicts:
            for n in cnotes:
                print(tr("Колекція: {0}", n))
    problems = order_problems(order, deps, disabled)
    report["profile"] = profile
    report["mod_order"] = order
    report["disabled"] = sorted(disabled)
    report["mod_deps"] = {n: sorted(d) for n, d in deps.items() if d}
    report["order_problems"] = problems
    if disabled:
        print(tr("Вимкнено в профілі: {0} мод(ів)", len(disabled)))
    on_order = [n for n in order if n not in disabled]
    on_mods = [m for m in dep_mods if m["name"] not in disabled]
    if args.conflicts:
        res = compute_conflicts(mods_root, index_dir, on_mods, on_order)
        skipped = [m["name"] + (tr(" (FOMOD ще не встановлено — запусти --fomod)") if m["layout"] == "fomod" else f" ({m['layout']})")
                   for m in view if m not in dep_mods
                   and not (m["layout"] == "fomod" and m.get("installed_as"))]
        print_conflicts(on_order, order_notes, problems, res, duplicate_plugins(on_mods), skipped)
        (output_root / "conflicts.tsv").write_text(
            tr("файл\tрівень\tперемагає\tперекриті\n") + "\n".join(res["files"]) + "\n", encoding="utf-8")
        (output_root / "conflicts.json").write_text(json.dumps(
            {"order": on_order, "pairs": res["pairs"], "identical_files": res["identical"],
             "duplicate_plugins": duplicate_plugins(on_mods), "order_problems": problems},
            ensure_ascii=False, indent=1), encoding="utf-8")
    elif problems:
        for p in problems:
            print(tr("УВАГА (порядок модів): {0}", p))

    deploy_ok = True
    if args.undeploy or args.deploy:
        game = find_game_dir(args.game)
        deploy_ok = False
        if not game:
            print(tr('\nПапку гри не знайдено. Вкажи її через --game "/шлях/до/Skyrim Special Edition"'))
        elif args.undeploy:
            if game_running():
                print(tr("\nГра запущена — закрий її перед відкатом."))
            elif not load_journal(output_root):
                print(tr("\nРозгортання не знайдено — нічого відкочувати."))
            else:
                print()
                undeploy(game, output_root)
                deploy_ok = True
        else:
            if problems:
                print(tr("\nУВАГА: порядок модів порушує залежності (див. вище) — плагіни можуть не завантажитись."))
            deploy_ok = deploy(game, mods_root, output_root, on_mods, on_order, args.dry_run,
                   use_loot=not args.no_loot, loot_update=args.loot_update, coll=coll,
                   profile=profile, pdir=pdir)

    if args.skse_launcher or args.skse_launcher_restore:
        game = find_game_dir(args.game)
        if not game:
            print(tr('\nПапку гри не знайдено. Вкажи її через --game "/шлях/до/Skyrim Special Edition"'))
        elif args.skse_launcher_restore:
            restore_skse_launcher(game, output_root)
        else:
            install_skse_launcher(game, output_root)

    if args.skse:
        game = find_game_dir(args.game)
        if not game:
            print(tr('\nSKSE: папку гри не знайдено. Вкажи її через --game "/шлях/до/Skyrim Special Edition"'))
        else:
            sk = skse_check(game, mods_root, [m for m in view if m["name"] not in disabled], args.skse_log, on_order)
            (output_root / "skse_report.json").write_text(json.dumps(sk, ensure_ascii=False, indent=1), encoding="utf-8")
            print_skse_report(sk)
            print("\n=== Proton / Steam ===")
            for l in check_proton_setup(game, find_srs(game, mods_root)):
                print(l)

    (output_root / "manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    print(tr("\nЗвіт збережено:"))
    print(f"  {output_root / 'manifest.json'}")
    print(tr("  {0}/ (списки файлів модів, .tsv.gz)", index_dir))
    print(tr("  {0} (порядок модів)", modlist_path))
    if args.conflicts:
        print(tr("  {0} (усі конфліктні файли)", output_root / 'conflicts.tsv'))
    if args.skse:
        print(f"  {output_root / 'skse_report.json'}")
    if args.profiles or args.profile_new or args.profile_delete:
        print_profiles(output_root, profile)
    if args.deploy and not args.dry_run and deploy_ok:
        print(tr("\nМоди розгорнуто в папку гри. Повний відкат: --undeploy"))
    elif not args.undeploy and not (args.deploy and not args.dry_run):
        print(tr("\nПримітка: без --deploy скрипт не змінює папку гри і plugins.txt."))
    return 0 if deploy_ok else 1

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(tr("\nЗупинено користувачем."))
        raise SystemExit(130)
