#!/usr/bin/env python3
# Skyrim Mod Engine v0.11 — archive analyzer and per-mod extractor
# Copyright (C) 2026 MrGabenDima
# SPDX-License-Identifier: GPL-3.0-or-later
# https://github.com/FartGarfild/SME-Skyrim-mod-engine — https://www.nexusmods.com/skyrimspecialedition/mods/197185
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
import tempfile
import sys
import textwrap
import xml.etree.ElementTree as ET
import zlib
import zipfile
from pathlib import Path, PurePosixPath
from datetime import datetime, timezone

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
    '\n=== ПЕРЕВІРКА ОНОВЛЕНЬ ===': '\n=== UPDATE CHECK ===',
    'Архівів з Nexus: {0} (модів: {1})': 'Archives from Nexus: {0} (mods: {1})',
    '  стара версія: папка {0}': '  old version: folder {0}',
    'Повна перевірка: список файлів кожного мода ({0} запит(ів) до Nexus)...': 'Full check: the file list of every mod ({0} request(s) to Nexus)...',
    'Оновлені на Nexus з останньої перевірки: {0}; з них у тебе: {1}': 'Updated on Nexus since the last check: {0}; of them yours: {1}',
    '  ПОМИЛКА Nexus: {0}': '  Nexus ERROR: {0}',
    'Нових версій немає.': 'No new versions.',
    'Є нові версії: {0}': 'New versions: {0}',
    'Оновити: --update-mod НАЗВА або --update-all (з Premium файли качаються самі)': 'Update: --update-mod NAME or --update-all (with Premium the files download by themselves)',
    'Файл застарів, а заміни автор не вказав: {0}': 'File outdated and the author named no replacement: {0}',
    'Не знайдено на Nexus (файл видалено або архів перейменовано): {0}': 'Not found on Nexus (file deleted or archive renamed): {0}',
    'Перевірити на Nexus, чи вийшли нові версії встановлених модів (full — перечитати всі моди, а не лише оновлені).': 'Check Nexus for new versions of installed mods (full — re-read every mod, not only updated ones).',
    'Оновити мод до знайденої нової версії: завантажити (Premium) і замінити на місці — порядок, увімкнення й вибір FOMOD лишаються.': 'Update a mod to the new version found: download (Premium) and replace in place — order, enabled state and FOMOD choices stay.',
    'Оновити всі моди, для яких знайдено нові версії (завантаження — з Premium).': 'Update every mod a new version was found for (download needs Premium).',
    '\n=== ОНОВЛЕННЯ МОДІВ: {0} ===': '\n=== MOD UPDATES: {0} ===',
    'видалена': 'deleted',
    '  старий архів {0}: {1}': '  old archive {0}: {1}',
    '  Нова версія мода «{0}» (було: {1}) — замінюю на місці, порядок і вибір лишаються': '  New version of «{0}» (was: {1}) — replacing in place, order and choices stay',
    '  Замінити «{0}» новою версією? [Y/n]: ': '  Replace «{0}» with the new version? [Y/n]: ',
    '\nОновлений FOMOD-мод «{0}»: перевстановлюю «{1}» зі збереженим вибором': '\nUpdated FOMOD mod «{0}»: reinstalling «{1}» with the saved choices',
    'Для «{0}» нової версії не знайдено — спершу --check-updates': 'No new version found for «{0}» — run --check-updates first',
    '{0}: новий архів уже є — розпакую': '{0}: the new archive is already here — unpacking',
    '{0}: без Premium завантаж файл на сайті («Mod Manager Download»), потім «Розпакувати нові»:\n  {1}': '{0}: without Premium download the file on the site (“Mod Manager Download”), then “Unpack new”:\n  {1}',
    '  У профілях {0} цей мод теж встановлено — перевстанови його там («Пакети» → «Вибрати варіанти» або --fomod-redo).': '  Profiles {0} have this mod installed too — reinstall it there (“Packages” → “Choose options” or --fomod-redo).',
    '  (новий файл визначено за назвою — перевір)': '  (new file matched by name — check it)',
    '  (архів уже завантажено — розпакуй, щоб застосувати)': '  (archive already downloaded — unpack to apply)',
    'Окремого GTK-інтерфейсу більше немає — відкриваю інтерфейс у вікні програми.': 'There is no separate GTK interface any more — opening the interface in the app window.',
    'Графічний інтерфейс у вікні програми (WebKit, Chromium/Vivaldi або Firefox), інакше вкладкою в браузері (також: подвійний клік по скрипту). web — одразу вкладкою.': 'Graphical interface in an app window (WebKit, Chromium/Vivaldi or Firefox), otherwise as a browser tab (also: double-click the script). web — a tab straight away.',
    'Skyrim.ini у префіксі не знайдено — запусти гру хоча б раз через Steam.': 'Skyrim.ini not found in the prefix — start the game through Steam at least once.',
    'Гра (або SKSE) зараз запущена — закрий її перед зміною Skyrim.ini.': 'The game (or SKSE) is running — close it before changing Skyrim.ini.',
    'Лог Papyrus {0} (Skyrim.ini). Діє з наступного запуску гри; лог: Documents/My Games/…/Logs/Script/Papyrus.0.log': 'Papyrus log {0} (Skyrim.ini). Takes effect on the next game start; log: Documents/My Games/…/Logs/Script/Papyrus.0.log',
    'Лог трохи навантажує гру — вимкни, коли знайдеш проблему.': 'The log adds a little load on the game — turn it off once you have found the problem.',
    'Увімкнути/вимкнути лог скриптів Papyrus у Skyrim.ini префікса (оригінал зберігається в backup).': "Turn the Papyrus script log on/off in the prefix's Skyrim.ini (the original is kept in backup).",
    'краш-логи': 'crash logs', 'папка гри': 'game folder',
    '\n=== ЛОГИ ГРИ Й МОДІВ ===': '\n=== GAME AND MOD LOGS ===',
    'Логів не знайдено — запусти гру хоча б раз через SKSE.': 'No logs found — start the game through SKSE at least once.',
    'Останній краш-лог: {0} ({1}){2}': 'Latest crash log: {0} ({1}){2}',
    ' — з останньої сесії': ' — from the last session',
    '  Найчастіше згадуються (підказка, не вирок): {0}': '  Mentioned most (a hint, not a verdict): {0}',
    '  помилок: {0}': '  errors: {0}', '  попереджень: {0}': '  warnings: {0}',
    '* — з останнього запуску гри (не старші за skse64.log більше ніж на годину).': '* — from the last game start (not more than an hour older than skse64.log).',
    'Лог Papyrus вимкнено (Skyrim.ini, [Papyrus] bEnableLogging=0) — для пошуку проблем зі скриптами його можна ввімкнути.': 'The Papyrus log is off (Skyrim.ini, [Papyrus] bEnableLogging=0) — turn it on to look for script problems.',
    'Логи гри, SKSE, модів, краш-логи й Papyrus: список з кількістю помилок і підказками з останнього краш-логу.': 'Logs of the game, SKSE, mods, crash logs and Papyrus: a list with error counts and hints from the latest crash log.',
    '{0}: вимкнено разом з його master {1}': '{0}: disabled together with its master {1}',
    'власний порядок плагінів профілю: {0} з {1}': "profile's own plugin order: {0} of {1}",
    ', вимкнено {0}': ', {0} disabled',
    'УВАГА (порядок плагінів): {0} стоїть вище свого master {1} — гра так не завантажить, ставлю після нього': 'WARNING (plugin order): {0} is above its master {1} — the game cannot load it that way, placing it after',
    'УВАГА (порядок плагінів): {0} — master (ESM/ESL), гра завжди вантажить його перед звичайними .esp (зокрема {1})': 'WARNING (plugin order): {0} is a master (ESM/ESL); the game always loads it before regular .esp files (such as {1})',
    'Гра (або SKSE) зараз запущена — закрий її, щоб застосувати порядок.': 'The game (or SKSE) is running — close it to apply the order.',
    'plugins.txt переписано — новий порядок діє з наступного запуску гри (файли не перерозгортались).': 'plugins.txt rewritten — the new order takes effect on the next game start (files were not redeployed).',
    'Порядок плагінів не прочитано: {0}': 'Could not read the plugin order: {0}',
    'Власний порядок плагінів профілю «{0}» збережено: {1} плагін(ів), вимкнено {2}.': 'Own plugin order of profile «{0}» saved: {1} plugin(s), {2} disabled.',
    'Власний порядок плагінів профілю «{0}» скинуто — знову діють LOOT і правила рушія.': 'Own plugin order of profile «{0}» reset — LOOT and the engine rules apply again.',
    'Новий порядок застосується при наступному розгортанні (--deploy).': 'The new order will be applied on the next deployment (--deploy).',
    'Власний порядок плагінів профілю (вище — раніше, нижче — перемагає): JSON-список, файл у форматі plugins.txt або reset. Якщо розгортання актуальне — одразу переписує plugins.txt.': "The profile's own plugin order (higher loads earlier, lower wins): a JSON list, a file in plugins.txt format, or reset. If the deployment is up to date, plugins.txt is rewritten right away.",
    '✔ Готово — можна грати.': '✔ Done — ready to play.',
    '✖ Автоналаштування не завершено — дивись журнал вище.': '✖ The automatic setup did not finish — see the log above.',
    '\n=== SKSE ===': '\n=== SKSE ===',
    'SKSE для гри {0} є.': 'SKSE for game {0} is present.',
    'SKSE не знайдено, але жоден мод його не потребує — пропускаю.': 'SKSE not found, but no mod needs it — skipping.',
    'УВАГА: SKSE не встановлено, а його потребують моди ({0}). Увійди в Nexus (Головна), щоб програма встановила його сама, або встанови SKSE вручну.': 'WARNING: SKSE is not installed, but mods need it ({0}). Log in to Nexus (Home) so the program installs it, or install SKSE by hand.',
    'УВАГА: SKSE встановити не вдалося — моди з SKSE-плагінами не працюватимуть.': 'WARNING: SKSE could not be installed — mods with SKSE plugins will not work.',
    'Якщо якийсь мод потребує SKSE, а його немає — завантажити (з Premium) або попередити.': 'If a mod needs SKSE and it is missing — download it (Premium) or warn.',
    '\n=== Видалення мода «{0}» ===': '\n=== Removing mod «{0}» ===',
    '  Архів «{0}» залишено (не розпаковуватиметься знову)': '  Archive «{0}» kept (will not be unpacked again)',
    '  Архів «{0}»: {1}': '  Archive «{0}»: {1}',
    '  Архів мода не знайдено серед розпакованих (мод міг бути покладений у папку вручну).': '  No archive of this mod among the unpacked ones (the mod may have been put into the folder by hand).',
    '  Архіву «{0}» вже немає': '  Archive «{0}» is already gone',
    '  Папка «{0}»: {1}': '  Folder «{0}»: {1}',
    '  Файли мода ще в папці гри: розгорни профіль знову, щоб їх прибрати.': "  The mod's files are still in the game folder: deploy the profile again to remove them.",
    'Видалити мод: папку, FOMOD-варіанти й архів (у кошик, якщо він є).': 'Remove a mod: its folder, FOMOD variants and archive (to the trash when there is one).',
    'Гра запущена — закрий її перед видаленням мода.': 'The game is running — close it before removing a mod.',
    'З --remove-mod: архів лишити (і більше не розпаковувати).': 'With --remove-mod: keep the archive (and do not unpack it again).',
    'Мода «{0}» немає в папці модів.': 'There is no mod «{0}» in the mods folder.',
    'видалено': 'deleted',
    'у кошику': 'in the trash',
    " (файлу поки немає — з'явиться після запуску гри через SKSE)": ' (no such file yet — it appears after the game is started through SKSE)',
    'skse64.log: стандартний шлях у префіксі Proton.': 'skse64.log: the default path in the Proton prefix.',
    'Додано в архіви: {0}': 'Added to archives: {0}',
    "Вікно інтерфейсу: {0}": "Interface window: {0}",
    "Nexus відповів не JSON: {0}": "Nexus did not answer with JSON: {0}",
    "Nexus дав посилання, яке не вдалося відкрити: {0}": "Nexus gave a link that could not be opened: {0}",
    "Пропущено: {0} ({1})": "Skipped: {0} ({1})",
    "Йде робота — зачекай, поки вона закінчиться.": "A job is running — wait until it finishes.",
    "Розпакувати лише цей архів (можна кілька разів), звідки завгодно — без копії в папку архівів.": "Unpack only this archive (can be repeated), from anywhere — without a copy in the archives folder.",
    "Не архів або не знайдено: {0}": "Not an archive or not found: {0}",
    'З --yes: якщо папка мода з такою назвою вже є — пропустити архів (skip) або розпакувати в папку з суфіксом (suffix).': 'With --yes: when a mod folder with that name exists — skip the archive (skip) or unpack into a suffixed folder (suffix).',
    "Запам'ятати папку гри, якщо її не знайдено автоматично ('default' — шукати самому).": "Remember the game folder when it is not found automatically ('default' — find it automatically).",
    "Запам'ятати свій шлях до skse64.log ('default' — стандартний у префіксі Proton).": "Remember an own path to skse64.log ('default' — the default one in the Proton prefix).",
    'Папка гри: {0}': 'Game folder: {0}',
    'Папку гри шукаю автоматично: {0}': 'Game folder is found automatically: {0}',
    'Сортування плагінів за LOOT: {0}': 'Plugin sorting with LOOT: {0}',
    "Сортувати плагіни за LOOT (on) чи лише за власними правилами (off); запам'ятовується.": 'Sort plugins with LOOT (on) or by own rules only (off); remembered.',
    'У «{0}» немає SkyrimSE.exe — це не папка гри. Нічого не змінено.': '«{0}» has no SkyrimSE.exe — it is not the game folder. Nothing was changed.',
    "  Скасовано — нічого не змінено.": "  Cancelled — nothing was changed.",
    "FOMOD-мода «{0}» немає в каталозі.": "There is no FOMOD mod «{0}» in the catalog.",
    "З --fomod/--fomod-redo: лише цей FOMOD-мод.": "With --fomod/--fomod-redo: only this FOMOD mod.",
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
    "Skyrim Mod Engine v0.11 — аналіз, розпакування, каталог модів, перевірка SKSE.":
        "Skyrim Mod Engine v0.11 — analysis, extraction, mod catalog, SKSE checks.",
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

SAVED_PATHS: dict = {"game_dir": None, "skse_log": None}  # remembered in settings.json (--set-game / --set-skse-log)

def load_saved_paths(output_root: Path) -> None:
    st = read_settings(output_root)
    SAVED_PATHS.update(game_dir=st.get("game_dir") or None, skse_log=st.get("skse_log") or None)

def is_game_dir(p: Path) -> bool:
    return p.is_dir() and ci_join(p, "SkyrimSE.exe") is not None

def find_game_dir(explicit: str | None) -> Path | None:
    # A folder chosen by the player is used as given (no silent fallback to another copy of the game).
    explicit = explicit or SAVED_PATHS.get("game_dir")
    # The script usually sits in the game folder itself: that folder wins over Steam libraries.
    cands = [Path(explicit).expanduser()] if explicit else [Path.cwd(), Path(__file__).resolve().parent] + [
        lib / "steamapps" / "common" / GAME_DIR_NAME for lib in steam_libraries()]
    for c in cands:
        if c.is_dir() and ci_join(c, "SkyrimSE.exe"):
            return c
    return None

def find_skse_log(game_dir: Path, explicit: str | None) -> Path | None:
    explicit = explicit or SAVED_PATHS.get("skse_log")
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
    try:
        with path.open("rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            i = mm.find(b"\xbd\x04\xef\xfe")
            if i < 0 or i + 16 > len(mm):
                return None
            ms, ls = struct.unpack_from("<II", mm, i + 8)
            return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except (OSError, ValueError):  # empty (e.g. a 0-byte stub left by a failed Steam update) or unreadable
        return None

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

# FOMOD questions for the graphical interface: with SME_FOMOD_IO=1 every group is sent as one
# "@@FOMOD@@ {json}" line on stdout and the answer is read as one JSON line from stdin.
FOMOD_IO = os.environ.get("SME_FOMOD_IO") == "1"
FOMOD_CTX: dict = {}

class FomodCancelled(Exception):
    pass

def ask_group_io(step_name: str, gname: str, gtype: str, scored: list[dict], default: list[int]) -> list[int]:
    req = [i for i, o in enumerate(scored) if o["type"] == "Required" and o["usable"]]
    err = None
    while True:
        q = {"mod": FOMOD_CTX.get("mod"), "module": FOMOD_CTX.get("module"), "step": step_name, "group": gname,
             "type": gtype, "rule": GROUP_RULES.get(gtype, gtype), "default": default, "required": req, "error": err,
             "options": [{"name": o["name"], "description": o["description"], "image": o.get("image"), "usable": o["usable"],
                          "kind": o["type"], "conflict": o.get("conflict"), "why": o.get("why") or []} for o in scored]}
        print("@@FOMOD@@ " + json.dumps(q, ensure_ascii=False), flush=True)
        line = sys.stdin.readline()
        if not line:
            return default
        try:
            ans = json.loads(line)
        except ValueError:
            return default
        if ans.get("cancel"):
            raise FomodCancelled()
        picked = sorted({int(i) for i in ans.get("picked", []) if 0 <= int(i) < len(scored)} | set(req))
        err = validate_pick(gtype, picked, scored) if gtype != "SelectAll" else None
        if not err:
            return picked if gtype != "SelectAll" else default

def ask_group(step_name: str, gname: str, gtype: str, scored: list[dict], default: list[int]) -> list[int]:
    if FOMOD_IO:
        return ask_group_io(step_name, gname, gtype, scored, default)
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
    FOMOD_CTX.update(mod=mod_dir.name, module=choices["module"])

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
                img_el = pl.find("image")
                img = ci_join(src_root, *[x for x in (img_el.get("path") or "").replace("\\", "/").split("/") if x]) \
                    if img_el is not None and img_el.get("path") else None
                scored.append({"name": pl.get("name", ""), "type": ftype, "score": sc, "why": why,
                               "usable": usable, "el": pl, "ops": pops, "image": str(img) if img and img.is_file() else None,
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
                   profile: str = DEFAULT_PROFILE, pdir: Path | None = None,
                   only_mod: str | None = None) -> tuple[list[str], dict[str, str]]:
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
    todo = [s for s in summaries if s["layout"] == "fomod" and (not only_mod or s["name"] == only_mod)]
    if only_mod and not todo:
        print(tr("FOMOD-мода «{0}» немає в каталозі.", only_mod))
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
        except FomodCancelled:
            print(tr("  Скасовано — нічого не змінено."))
            continue
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

def send_to_trash(path: Path) -> str:
    """The desktop's trash when there is one (a mistake can be undone), otherwise a plain delete."""
    for cmd in (["gio", "trash", str(path)], ["kioclient6", "move", str(path), "trash:/"], ["kioclient5", "move", str(path), "trash:/"]):
        if shutil.which(cmd[0]):
            try:
                if subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  timeout=120, check=False).returncode == 0 and not path.exists():
                    return "trash"
            except (OSError, subprocess.SubprocessError):
                pass
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()
    return "deleted"

def archive_volumes(archive: Path) -> list[Path]:
    """An archive and, for "name.part1.rar", the other parts next to it."""
    if not FIRST_VOLUME_RE.search(archive.name):
        return [archive]
    stem = FIRST_VOLUME_RE.sub("", archive.name)
    return sorted(p for p in archive.parent.glob(glob_escape(stem) + ".part*.rar") if p.is_file())

def glob_escape(s: str) -> str:
    return re.sub(r"([*?\[])", r"[\1]", s)

def remove_mod(output_root: Path, mods_root: Path, archives_root: Path, name: str, keep_archive: bool) -> int:
    """Remove a mod completely: its folder, its FOMOD variants, its lines in every profile and saved FOMOD
    choices, and its archive (to the trash) unless keep_archive — then the archive is marked so that the
    next scan does not unpack it again."""
    m = FOMOD_OUT_RE.match(name)
    base = m.group(1) if m and (mods_root / m.group(1)).is_dir() else name
    if not (mods_root / base).is_dir() and not (mods_root / name).is_dir():
        print(tr("Мода «{0}» немає в папці модів.", name))
        return 1
    if game_running():
        print(tr("Гра запущена — закрий її перед видаленням мода."))
        return 1
    folders = sorted({base} | {p.name for p in mods_root.iterdir() if p.is_dir() and (fm := FOMOD_OUT_RE.match(p.name)) and fm.group(1) == base}
                     | ({name} if (mods_root / name).is_dir() else set()))
    folders = [f for f in folders if (mods_root / f).is_dir()]
    print(tr("\n=== Видалення мода «{0}» ===", base))
    j = load_journal(output_root)
    in_game = [f for f in folders if j and f in (j.get("order") or [])]
    for f in folders:
        how = send_to_trash(mods_root / f)
        print(tr("  Папка «{0}»: {1}", f, tr("у кошику") if how == "trash" else tr("видалено")))
    # archives that produced it
    state_p = output_root / "processed_archives.json"
    try:
        state = json.loads(state_p.read_text(encoding="utf-8")) if state_p.exists() else {}
    except Exception:
        state = {}
    cache_p = output_root / "md5_cache.json"
    try:
        cache = json.loads(cache_p.read_text(encoding="utf-8")) if cache_p.exists() else {}
    except Exception:
        cache = {}
    found = False
    for rel, rec in list(state.items()):
        dest = rec.get("destination")
        if not dest or Path(dest).name not in folders:
            continue
        found = True
        ap = Path(rel) if Path(rel).is_absolute() else archives_root / rel
        inside = not Path(rel).is_absolute()
        if keep_archive or not inside:
            rec["removed"] = True  # keep the file, but do not unpack it again
            if ap.exists():
                st_ = ap.stat()
                rec.update(size=st_.st_size, mtime_ns=st_.st_mtime_ns)
            print(tr("  Архів «{0}» залишено (не розпаковуватиметься знову)", ap.name) if ap.exists() else tr("  Архіву «{0}» вже немає", ap.name))
            continue
        for v in archive_volumes(ap) if ap.exists() else []:
            how = send_to_trash(v)
            print(tr("  Архів «{0}»: {1}", v.name, tr("у кошику") if how == "trash" else tr("видалено")))
            cache.pop(str(v.relative_to(archives_root)), None)
        state.pop(rel, None)
    if not found:
        print(tr("  Архів мода не знайдено серед розпакованих (мод міг бути покладений у папку вручну)."))
    state_p.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    if cache_p.exists():
        cache_p.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    # every profile: mod order lines and saved FOMOD choices
    gone = set(folders)
    for prof in list_profiles(output_root):
        pdir = profile_dir(output_root, prof)
        ml = pdir / "modlist.txt"
        if ml.exists():
            lines = ml.read_text(encoding="utf-8").splitlines()
            keep = [l for l in lines if l.strip().lstrip("-").strip() not in gone]
            if len(keep) != len(lines):
                ml.write_text("\n".join(keep) + "\n", encoding="utf-8")
        for f in (pdir / "fomod").glob("*.json") if (pdir / "fomod").is_dir() else []:
            try:
                if json.loads(f.read_text(encoding="utf-8")).get("mod") == base:
                    f.unlink()
            except Exception:
                pass
    if in_game:
        print(tr("  Файли мода ще в папці гри: розгорни профіль знову, щоб їх прибрати."))
    return 0

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

USER_PLUGIN_ORDER = "plugin_order.json"  # per profile: the user's own load order, wins over LOOT and the collection

def plugin_master_flag(name: str, path: Path | None) -> bool:
    """The game loads these with the masters: .esm/.esl, or an .esp with the ESM flag in its header."""
    if name.lower().endswith((".esm", ".esl")):
        return True
    try:
        with path.open("rb") as f:
            h = f.read(12)
        return len(h) == 12 and h[:4] == b"TES4" and bool(struct.unpack_from("<I", h, 8)[0] & 0x1)
    except (OSError, AttributeError):
        return False

def load_user_plugin_order(pdir: Path | None) -> dict | None:
    if pdir is None:
        return None
    try:
        d = json.loads((pdir / USER_PLUGIN_ORDER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict) or not isinstance(d.get("order"), list) or not d["order"]:
        return None
    return {"order": [str(x) for x in d["order"] if str(x).strip()],
            "disabled": [str(x) for x in d.get("disabled") or []]}

def parse_plugin_order_arg(value: str) -> dict | None:
    """--plugin-order: JSON {"order": [...], "disabled": [...]} or a JSON list, a file with either
    of these, or a text file (one plugin per line, "*" = active, as in plugins.txt). None = reset."""
    v = value.strip()
    if v.lower() in ("reset", "none", "loot"):
        return None
    if not v.startswith(("{", "[")):
        p = Path(v).expanduser()
        txt = p.read_text(encoding="utf-8-sig", errors="replace")
        if not txt.lstrip().startswith(("{", "[")):
            order, off = [], []
            for l in txt.splitlines():
                l = l.strip()
                if not l or l.startswith("#"):
                    continue
                name = l.lstrip("*").strip()
                order.append(name)
                if not l.startswith("*") and any(x.strip().startswith("*") for x in txt.splitlines()):
                    off.append(name)
            return {"order": order, "disabled": off}
        v = txt
    d = json.loads(v)
    if isinstance(d, list):
        d = {"order": d}
    return {"order": [str(x) for x in d.get("order") or [] if str(x).strip()],
            "disabled": [str(x) for x in d.get("disabled") or []]}

def apply_user_plugin_order(load: list, uo: dict) -> tuple[list, set[str], list[str]]:
    """The user's order for the plugins they listed (unlisted ones keep their place after their
    predecessor). Two things the game itself enforces win over it: master-flagged plugins load
    before all regular ones, and a plugin loads after its own masters."""
    order = uo.get("order") or []
    if not order:
        return load, set(), []
    pos = {n.lower(): k for k, n in enumerate(order)}
    flag = {it[0].lower(): plugin_master_flag(it[0], it[1]) for it in load}
    out, disabled, _ = apply_collection_plugin_order(load, {"plugins": [{"name": n} for n in order]},
                                                     lambda it: flag[it[0].lower()])
    present = {it[0].lower() for it in load}
    disabled = {n.lower() for n in uo.get("disabled") or [] if n.lower() in present}
    notes = [tr("власний порядок плагінів профілю: {0} з {1}", sum(1 for it in load if it[0].lower() in pos), len(load))
             + (tr(", вимкнено {0}", len(disabled)) if disabled else "")]
    for name, _, masters in load:
        k = pos.get(name.lower())
        if k is None:
            continue
        for m in masters:
            j = pos.get(m.lower())
            if j is not None and j > k:
                notes.append(tr("УВАГА (порядок плагінів): {0} стоїть вище свого master {1} — гра так не завантажить, ставлю після нього", name, m))
        if flag.get(name.lower()):
            later_regular = [n for n in order[:k] if n.lower() in flag and not flag[n.lower()]]
            if later_regular:
                notes.append(tr("УВАГА (порядок плагінів): {0} — master (ESM/ESL), гра завжди вантажить його перед звичайними .esp (зокрема {1})", name, later_regular[-1]))
    return out, disabled, notes

def final_plugin_load(game_dir: Path, mods_root: Path, output_root: Path, mods: list[dict], order: list[str],
                      data_files: dict, root_files: dict, use_loot: bool, loot_update: bool,
                      coll: dict | None, pdir: Path | None) -> tuple[list, set[str], list[str]]:
    """Engine rules -> LOOT -> collection author's order -> the user's own order (each step can only
    reorder within what the game allows). Returns (load order, disabled plugins, report lines)."""
    load = plugin_load_order(mods_root, mods, order)
    load, lines = order_plugins_with_loot(game_dir, output_root, load, data_files, root_files, use_loot, loot_update)
    disabled: set[str] = set()
    if coll:
        load, disabled, cnotes = apply_collection_plugin_order(load, coll, lambda it: plugin_master_flag(it[0], it[1]))
        lines = lines[:1] + cnotes + lines[1:]
    uo = load_user_plugin_order(pdir)
    if uo:
        load, udis, unotes = apply_user_plugin_order(load, uo)
        disabled = (disabled - {n.lower() for n in uo["order"]}) | udis  # the user's choice wins for listed ones
        lines = lines[:1] + unotes + lines[1:]
    if disabled:  # a plugin whose master is switched off would stop the game at start ("missing masters")
        extra = []
        for name, _, masters in load:
            off = [m for m in masters if m.lower() in disabled]
            if off and name.lower() not in disabled:
                disabled.add(name.lower())
                extra.append(tr("{0}: вимкнено разом з його master {1}", name, ", ".join(off)))
        lines = lines[:1] + extra + lines[1:]
    return load, disabled, lines

def reapply_plugin_order(game_dir: Path, mods_root: Path, output_root: Path, mods: list[dict], order: list[str],
                         use_loot: bool, coll: dict | None, profile: str, pdir: Path) -> bool:
    """Rewrite plugins.txt of the current deployment with the new order, without redeploying files.
    Only when that deployment is this profile's and has exactly these mods; otherwise False."""
    journal = load_journal(output_root)
    if not journal or not journal.get("complete", True) or journal.get("profile", DEFAULT_PROFILE) != profile:
        return False
    if [x for x in journal.get("order", []) if x != OVERWRITE_NAME] != order:
        return False
    if game_running():
        print(tr("Гра (або SKSE) зараз запущена — закрий її, щоб застосувати порядок."))
        return False
    ow = overwrite_summary(pdir)
    if ow:
        mods, order = mods + [ow], order + [OVERWRITE_NAME]
    data_files, root_files = deploy_plan(mods_root, mods, order)
    load, disabled, lines = final_plugin_load(game_dir, mods_root, output_root, mods, order, data_files,
                                              root_files, use_loot, False, coll, pdir)
    for l in lines[:2] + [x for x in lines[2:] if not x.startswith("LOOT: ")]:
        print(l)
    msgs = write_plugins_txt(game_dir, output_root, journal, load, False, disabled)
    for m in msgs:
        print(f"  {m}")
    journal["plugins"] = [it[0] for it in load]
    journal["plugins_disabled"] = sorted(disabled)
    save_journal(output_root, journal)
    print(tr("plugins.txt переписано — новий порядок діє з наступного запуску гри (файли не перерозгортались)."))
    return True

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
    load, disabled, loot_lines = final_plugin_load(game_dir, mods_root, output_root, mods, order, data_files,
                                                   root_files, use_loot, loot_update, coll, pdir)
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
            journal["plugins_disabled"] = sorted(disabled)
            journal["complete"] = True
            (output_root / REDEPLOY_FILE).unlink(missing_ok=True)
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

# ===================================================================================
# Logs written by the game, SKSE and mods (read-only: the program never changes them)
# ===================================================================================
GAME_LOG_ERR_RE = re.compile(r"\[(?:error|critical|E|C)\]|\b(?:error|errors|exception|failed|failure|fatal|crash(?:ed)?|"
                             r"unable to|cannot|can't|could not|couldn't|missing|not found)\b", re.I)
GAME_LOG_WARN_RE = re.compile(r"\[(?:warning|warn|W)\]|\bwarn(?:ing)?s?\b", re.I)
GAME_LOG_NOISE_RE = re.compile(r"\b0 errors?\b|\berrors?: 0\b|no errors?\b", re.I)
GAME_LOG_SYSTEM_DLLS = {
    "ntdll.dll", "kernel32.dll", "kernelbase.dll", "ucrtbase.dll", "user32.dll", "win32u.dll", "gdi32.dll",
    "msvcrt.dll", "msvcp140.dll", "vcruntime140.dll", "vcruntime140_1.dll", "d3d11.dll", "dxgi.dll",
    "d3dcompiler_47.dll", "xinput1_3.dll", "steam_api64.dll", "combase.dll", "ole32.dll", "rpcrt4.dll",
    "advapi32.dll", "sechost.dll", "bcrypt.dll", "dbghelp.dll", "winmm.dll", "dsound.dll", "xaudio2_7.dll",
    "wined3d.dll", "dxvk_d3d11.dll", "version.dll", "shell32.dll", "ws2_32.dll", "crashloggersse.dll",
    "trainwreck.dll"}
GAME_LOG_MAX_FILES = 400

def game_log_files(game_dir: Path) -> list[dict]:
    """Every log we know of, newest first: SKSE and its plugins, crash logs, Papyrus, Proton, Data/SKSE."""
    _, docs = prefix_dirs(game_dir)
    out: list[dict] = []
    seen: set[str] = set()

    def add(p: Path, group: str) -> None:
        try:
            if not p.is_file() or str(p) in seen:
                return
            st = p.stat()
        except OSError:
            return
        seen.add(str(p))
        out.append({"path": str(p), "name": p.name, "group": group, "size": st.st_size, "mtime": st.st_mtime})

    def crashy(name: str) -> bool:  # crash-2026-11-02-18-46-01.log (Crash Logger, Trainwreck); not CrashLogger.log itself
        return re.match(r"crash[-_ ]?\d", name.lower()) is not None

    skse = ci_join(docs, "SKSE") if docs.exists() else None
    if skse:
        for dp, dns, fns in os.walk(skse):
            depth = len(Path(dp).relative_to(skse).parts)
            if depth >= 2:
                dns[:] = []
            for fn in fns:
                if fn.lower().endswith((".log", ".txt")):
                    sub = Path(dp).relative_to(skse).parts
                    add(Path(dp) / fn, "crash" if crashy(fn) or (sub and sub[0].lower() == "crashlogs") else "skse")
    explicit = SAVED_PATHS.get("skse_log")
    if explicit:
        add(Path(explicit).expanduser(), "skse")
    script = ci_join(docs, "Logs", "Script") if docs.exists() else None
    if script:
        for p in list(script.glob("*.log")) + list(script.glob("User/*.log")):
            add(p, "papyrus")
    gdata = ci_join(game_dir, "Data")
    if gdata:
        sp = ci_join(gdata, "SKSE", "Plugins")
        if sp:
            for p in sp.rglob("*.log"):
                add(p, "data")
        nsf = ci_join(gdata, "NetScriptFramework", "Crash")
        if nsf:
            for p in nsf.glob("*.txt"):
                add(p, "crash")
    for p in game_dir.glob("*.log"):
        add(p, "game")
    home = Path.home()
    for p in home.glob(f"steam-{SKYRIM_APPID}*.log"):
        add(p, "proton")
    out.sort(key=lambda x: -x["mtime"])
    return out[:GAME_LOG_MAX_FILES]

def read_log_tail(path: Path, limit: int = 2 << 20) -> tuple[str, bool]:
    """Last `limit` bytes as text (logs are UTF-8 or the Windows code page), and whether it was cut."""
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - limit))
        data = f.read()
    cut = size > limit
    if cut:
        data = data[data.find(b"\n") + 1:]
    if data.startswith(b"\xff\xfe") or data[1:200:2].count(0) > 50:  # a few tools write UTF-16
        try:
            return data.decode("utf-16", "replace"), cut
        except Exception:
            pass
    try:
        return data.decode("utf-8"), cut
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace"), cut

GAME_LOG_TAG_RE = re.compile(r"\[(error|critical|err|E|C|warning|warn|W|info|debug|trace|I|D|T)\]", re.I)

def log_line_level(line: str) -> str:
    m = GAME_LOG_TAG_RE.search(line)  # spdlog-style level tag of SKSE plugins: it decides
    if m:
        tag = m.group(1).lower()
        return "e" if tag in ("error", "critical", "err", "e", "c") else "w" if tag in ("warning", "warn", "w") else ""
    if GAME_LOG_NOISE_RE.search(line):
        return ""
    e, w = GAME_LOG_ERR_RE.search(line), GAME_LOG_WARN_RE.search(line)
    if e and w:  # "warning: ... cannot be initialized" is a warning: the first keyword is the level
        return "e" if e.start() < w.start() else "w"
    return "e" if e else "w" if w else ""

def crash_log_summary(text: str, known: set[str] | None = None) -> dict:
    """What a crash log points at: the exception line and the mod DLLs / plugins it mentions most.
    Only hints — a name in a crash log is not proof that this mod caused the crash. Names of installed
    plugins and SKSE DLLs (`known`, may contain spaces) are counted exactly; others by a simple pattern."""
    exc = next((l.strip() for l in text.splitlines() if re.search(r"unhandled exception|exception_[a-z_]+|access violation", l, re.I)), "")
    low_text = text.lower()
    names: dict[str, int] = {}
    skip = lambda low: low in GAME_LOG_SYSTEM_DLLS or low in BASE_MASTERS or low.startswith(("skse64_", "api-ms-win"))
    covered = set()
    for n in known or ():
        low = n.lower()
        if skip(low):
            continue
        c = low_text.count(low)
        if c:
            names[n] = c
            covered.add(low)
    for m in re.finditer(r'"([^"\n]{1,120}?\.(?:dll|esp|esm|esl))"', text, re.I):  # quoted names may have spaces
        n = m.group(1).strip()
        low = n.lower()
        if skip(low) or low in covered:
            continue
        names[n] = names.get(n, 0) + 1
    quoted = {k.lower() for k in names} - covered
    for low in quoted:  # the same name also unquoted elsewhere in the log
        n = next(k for k in names if k.lower() == low)
        names[n] = max(names[n], low_text.count(low))
    covered |= quoted
    for m in re.finditer(r"(?<![\w.'&+()\-])([A-Za-z0-9_][A-Za-z0-9_.'&+()\-]{0,80}\.(?:dll|esp|esm|esl))\b", text, re.I):
        n = m.group(1)
        low = n.lower()
        if skip(low) or any(k.endswith(low) for k in covered) or re.fullmatch(r"(?:0x)?[0-9a-f]+", low.rsplit(".", 1)[0]):
            continue
        names[n] = names.get(n, 0) + 1
    top = sorted(names.items(), key=lambda kv: -kv[1])[:8]
    return {"exception": exc[:300], "mentions": [{"name": n, "count": c} for n, c in top]}

def installed_plugin_names(game_dir: Path) -> set[str]:
    gdata = ci_join(game_dir, "Data")
    out: set[str] = set()
    if gdata:
        out |= {p.name for p in gdata.iterdir() if p.suffix.lower() in PLUGIN_EXTS}
        sp = ci_join(gdata, "SKSE", "Plugins")
        if sp:
            out |= {p.name for p in sp.iterdir() if p.suffix.lower() == ".dll"}
    return out

def game_log_overview(game_dir: Path, with_counts: bool = True, cache: dict | None = None) -> dict:
    """Log list with error/warning counts, the start of the last game session (skse64.log) and the newest crash."""
    files = game_log_files(game_dir)
    skse_log = find_skse_log(game_dir, None)
    # skse64.log is rewritten on every start through SKSE; plugin logs are too. A log much older than
    # it (an hour of margin for long loading) was left by an earlier session or by a removed mod.
    session = skse_log.stat().st_mtime if skse_log else None
    known = None
    for f in files:
        f["fresh"] = session is not None and f["mtime"] >= session - 3600
        if not with_counts:
            continue
        key = (f["path"], f["mtime"], f["size"])
        c = cache.get(f["path"]) if cache is not None else None
        if c and c[0] == key:
            f.update(c[1])
            continue
        info = {"errors": 0, "warnings": 0}
        try:
            text, _ = read_log_tail(Path(f["path"]), 4 << 20)
            for l in text.splitlines():
                lv = log_line_level(l)
                if lv == "e":
                    info["errors"] += 1
                elif lv == "w":
                    info["warnings"] += 1
            if f["group"] == "crash":
                if known is None:
                    known = installed_plugin_names(game_dir)
                info["crash"] = crash_log_summary(text, known)
        except OSError:
            info["unreadable"] = True
        if cache is not None:
            cache[f["path"]] = (key, info)
        f.update(info)
    crash = next((f for f in files if f["group"] == "crash"), None)
    return {"files": files, "session": session, "skse_log": str(skse_log) if skse_log else None,
            "last_crash": crash, "papyrus_on": papyrus_logging_on(game_dir)}

def papyrus_logging_on(game_dir: Path) -> bool | None:
    """[Papyrus] bEnableLogging in Skyrim.ini / SkyrimCustom.ini of the prefix (None = no ini found)."""
    _, docs = prefix_dirs(game_dir)
    val = None
    for name in ("Skyrim.ini", "SkyrimCustom.ini"):
        ini = ci_join(docs, name) if docs.exists() else None
        if not ini:
            continue
        sec = ""
        for l in ini.read_text(encoding="utf-8", errors="replace").splitlines():
            l = l.strip()
            if l.startswith("["):
                sec = l.lower()
            elif sec == "[papyrus]" and l.lower().replace(" ", "").startswith("benablelogging="):
                val = l.split("=", 1)[1].strip() == "1"
        if val is None:
            val = False
    return val

PAPYRUS_LOG_KEYS = ("bEnableLogging", "bEnableTrace", "bLoadDebugInformation")

def set_papyrus_logging(game_dir: Path, output_root: Path, on: bool) -> bool:
    """Switch the Papyrus script log in Skyrim.ini of the prefix ([Papyrus] section). The first change
    keeps the original as mod_engine_output/backup/Skyrim.ini.original."""
    _, docs = prefix_dirs(game_dir)
    ini = ci_join(docs, "Skyrim.ini") if docs.exists() else None
    if ini is None:
        print(tr("Skyrim.ini у префіксі не знайдено — запусти гру хоча б раз через Steam."))
        return False
    if game_running():
        print(tr("Гра (або SKSE) зараз запущена — закрий її перед зміною Skyrim.ini."))
        return False
    raw = ini.read_bytes()
    nl = "\r\n" if b"\r\n" in raw else "\n"
    lines = raw.decode("utf-8", errors="replace").splitlines()
    bk = output_root / "backup" / "Skyrim.ini.original"
    if not bk.exists():
        bk.parent.mkdir(parents=True, exist_ok=True)
        bk.write_bytes(raw)
    val = "1" if on else "0"
    out, sec, done, in_pap = [], "", set(), False
    for l in lines:
        st = l.strip()
        if st.startswith("["):
            if in_pap:
                out += [f"{k}={val}" for k in PAPYRUS_LOG_KEYS if k.lower() not in done]
                done |= {k.lower() for k in PAPYRUS_LOG_KEYS}
            in_pap = st.lower() == "[papyrus]"
            out.append(l)
            continue
        key = st.split("=", 1)[0].strip().lower() if "=" in st else ""
        if in_pap and key in {k.lower() for k in PAPYRUS_LOG_KEYS}:
            out.append(f"{st.split('=', 1)[0].strip()}={val}")
            done.add(key)
            continue
        out.append(l)
    if in_pap:
        out += [f"{k}={val}" for k in PAPYRUS_LOG_KEYS if k.lower() not in done]
        done |= {k.lower() for k in PAPYRUS_LOG_KEYS}
    if not done:
        out += ["", "[Papyrus]"] + [f"{k}={val}" for k in PAPYRUS_LOG_KEYS]
    ini.write_bytes((nl.join(out) + nl).encode("utf-8"))
    print(tr("Лог Papyrus {0} (Skyrim.ini). Діє з наступного запуску гри; лог: Documents/My Games/…/Logs/Script/Papyrus.0.log", tr("увімкнено") if on else tr("вимкнено")))
    if on:
        print(tr("Лог трохи навантажує гру — вимкни, коли знайдеш проблему."))
    return True

def print_game_logs(game_dir: Path) -> None:
    ov = game_log_overview(game_dir)
    groups = {"crash": tr("краш-логи"), "skse": "SKSE", "papyrus": "Papyrus", "data": "Data/SKSE/Plugins",
              "game": tr("папка гри"), "proton": "Proton"}
    print(tr("\n=== ЛОГИ ГРИ Й МОДІВ ==="))
    if not ov["files"]:
        print(tr("Логів не знайдено — запусти гру хоча б раз через SKSE."))
        return
    if ov["last_crash"]:
        c = ov["last_crash"]
        print(tr("Останній краш-лог: {0} ({1}){2}", c["name"], datetime.fromtimestamp(c["mtime"]).strftime("%Y-%m-%d %H:%M"),
                 tr(" — з останньої сесії") if c.get("fresh") else ""))
        cs = c.get("crash") or {}
        if cs.get("exception"):
            print(f"  {cs['exception']}")
        if cs.get("mentions"):
            print(tr("  Найчастіше згадуються (підказка, не вирок): {0}", ", ".join(f"{m['name']} ×{m['count']}" for m in cs["mentions"])))
    for f in ov["files"]:
        mark = "*" if f.get("fresh") else " "
        print(f"{mark} [{groups.get(f['group'], f['group'])}] {f['name']}  {human_size(f['size'])}  "
              f"{datetime.fromtimestamp(f['mtime']).strftime('%Y-%m-%d %H:%M')}"
              + (tr("  помилок: {0}", f["errors"]) if f.get("errors") else "")
              + (tr("  попереджень: {0}", f["warnings"]) if f.get("warnings") else ""))
        print(f"      {f['path']}")
    print(tr("* — з останнього запуску гри (не старші за skse64.log більше ніж на годину)."))
    if ov["papyrus_on"] is False:
        print(tr("Лог Papyrus вимкнено (Skyrim.ini, [Papyrus] bEnableLogging=0) — для пошуку проблем зі скриптами його можна ввімкнути."))

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
APP_VERSION = "0.11"
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
    url = safe_url((path if path.startswith("http") else NEXUS_API + path) + (("?" + urllib.parse.urlencode(params)) if params else ""))
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
    except (TimeoutError, OSError) as ex:  # a read that timed out, a dropped connection
        raise NexusError(tr("Немає з'єднання з Nexus: {0}", ex))
    except ValueError as ex:  # not JSON (an HTML error page from a proxy or Cloudflare)
        raise NexusError(tr("Nexus відповів не JSON: {0}", ex))

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

def safe_url(url: str) -> str:
    """Percent-encode what a URL must not contain raw. The Nexus CDN returns links with spaces in the
    file name ("/cdn/1704/3057/Skyrim Unlimited Rings ....7z?..."), which Python's http.client refuses."""
    import urllib.parse
    p = urllib.parse.urlsplit(url)
    path = urllib.parse.quote(p.path, safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(p.query, safe="=&%/:@!$'()*+,;-._~?")
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path, query, p.fragment))

def http_download(url: str, part: Path, size: int = 0) -> None:
    """Stream url into part, resuming a partial file; retries a dropped connection twice."""
    import urllib.request
    import urllib.error
    import http.client
    url = safe_url(url)
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
        except http.client.InvalidURL as ex:
            raise NexusError(tr("Nexus дав посилання, яке не вдалося відкрити: {0}", ex))
        except (urllib.error.URLError, OSError, http.client.HTTPException) as ex:
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
        remember_nexus_source(archives_root, dest.name, mod, file_id, info)
        print(tr("  {0}: уже є в mods-archives", fname))
        return dest
    params = {"key": nxm_key, "expires": expires} if nxm_key and expires else None
    links = nexus_get(f"/v1/games/{game}/mods/{mod}/files/{file_id}/download_link.json", key, params)
    if not isinstance(links, list) or not links or not isinstance(links[0], dict) or not links[0].get("URI"):
        raise NexusError(tr("Nexus не дав посилання на завантаження") + (f": {str(links)[:200]}" if links else ""))
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
    remember_nexus_source(archives_root, dest.name, mod, file_id, info)
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

def nexus_graphql(query: str, variables: dict, key: str | None) -> dict:
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
        (pdir / "collection_source.json").write_text(json.dumps({"slug": slug, "revision": info["revision"], "name": info["name"]},
                                                                ensure_ascii=False), encoding="utf-8")
        shutil.rmtree(staging, ignore_errors=True)
    except NexusError as ex:
        print(tr("ПОМИЛКА: {0}", ex))
        return None
    req = sum(1 for m in data["mods"] if not m.get("optional"))
    print(tr("Колекцію збережено: {0} модів (обов'язкових {1}). Вона використовуватиметься в наступних запусках.", len(data["mods"]), req))
    if not acc.get("is_premium"):
        print(tr("Без Premium архіви качаються кнопкою «Mod Manager Download»: список посилань — у collection_report.txt і в GUI (сторінка «Колекція»)."))
    return data

# --- Mod browser: search Nexus from the program ---------------------------------------------------
# GraphQL v2 search (works without a key); fields the API does not know are dropped and retried, so
# a schema change degrades the result instead of breaking it. Without search: v1 lists (need a key).
NEXUS_MOD_FIELDS = ["modId", "name", "summary", "version", "author", "pictureUrl", "thumbnailUrl",
                    "endorsements", "downloads", "updatedAt", "adultContent"]
NEXUS_SORTS = {"endorsements": "endorsements", "downloads": "downloads", "updated": "updatedAt", "new": "createdAt"}

def nexus_search(query: str = "", sort: str = "endorsements", offset: int = 0, count: int = 20,
                 key: str | None = None, adult: bool = False) -> dict:
    """Filter shapes follow the official Nexus Discord bot (name WILDCARD, adultContent EQUALS false).
    No hits by name -> one more try with the stemmed (word-form) name search."""
    fields = list(NEXUS_MOD_FIELDS)
    base: dict = {"gameDomainName": [{"value": NEXUS_GAME, "op": "EQUALS"}]}
    if not adult:
        base["adultContent"] = [{"value": False, "op": "EQUALS"}]
    text = query.strip()
    variants = [dict(base, name=[{"value": text, "op": "WILDCARD"}]), dict(base, nameStemmed=[{"value": text}])] + \
        ([dict(base, name=[{"value": f"*{text}*", "op": "WILDCARD"}])] if "*" not in text else []) if text else [base]
    sort_key = NEXUS_SORTS.get(sort, "endorsements")
    last = None
    vi = 0
    for _ in range(12):
        flt = variants[vi]
        q = ("query($filter: ModsFilter, $sort: [ModsSort!], $offset: Int, $count: Int) {"
             " mods(filter: $filter, sort: $sort, offset: $offset, count: $count) { totalCount nodes { %s } } }" % " ".join(fields))
        try:
            d = nexus_graphql(q, {"filter": flt, "sort": [{sort_key: {"direction": "DESC"}}], "offset": offset, "count": count}, key)
        except NexusError as ex:
            last = str(ex)
            m = re.search(r'Cannot query field "(\w+)"', last) or re.search(r"field '(\w+)'", last, re.I)
            if m and m.group(1) in fields and m.group(1) != "modId":
                fields.remove(m.group(1))  # a renamed/removed field costs only that column
                continue
            if "adultContent" in last and "adultContent" in flt:
                variants = [{k: v for k, v in x.items() if k != "adultContent"} for x in variants]
                continue
            if sort_key != "endorsements" and sort_key in last:
                sort_key = "endorsements"
                continue
            if vi + 1 < len(variants):
                vi += 1
                continue
            raise
        mods = (d.get("mods") or {})
        nodes = mods.get("nodes") or []
        if not nodes and offset == 0 and vi + 1 < len(variants):
            vi += 1
            continue
        out = []
        for n in nodes:
            out.append({"mod_id": n.get("modId"), "name": n.get("name"), "summary": n.get("summary") or "",
                        "version": n.get("version"), "author": n.get("author"),
                        "picture": n.get("thumbnailUrl") or n.get("pictureUrl"), "endorsements": n.get("endorsements"),
                        "downloads": n.get("downloads"), "updated": n.get("updatedAt"), "adult": n.get("adultContent")})
        total = mods.get("totalCount")
        total = int(total) if isinstance(total, (int, float)) else None  # unknown: pages go on while they are full
        return {"total": total, "mods": out, "source": "graphql",
                "has_more": (offset + len(nodes) < total) if total is not None else len(nodes) >= count}
    raise NexusError(last or "search failed")

NEXUS_COLLECTION_FIELDS = ["slug", "name", "summary", "endorsements", "totalDownloads", "user { name }", "category { name }",
                           "updatedAt", "tileImage { thumbnailUrl }",
                           "latestPublishedRevision { revisionNumber modCount adultContent totalSize }"]
NEXUS_COLLECTION_SORTS = {"endorsements": "endorsements", "downloads": "downloads", "updated": "updatedAt", "new": "updatedAt"}

def nexus_collections_search(query: str = "", sort: str = "endorsements", offset: int = 0, count: int = 20,
                             key: str | None = None, adult: bool = False) -> dict:
    """Collections for this game (GraphQL collectionsV2). A field the API does not know is dropped and the
    query repeated, as with the mod search."""
    fields = list(NEXUS_COLLECTION_FIELDS)
    flt: dict = {"gameDomain": [{"value": NEXUS_GAME, "op": "EQUALS"}]}
    if query.strip():
        flt["generalSearch"] = [{"value": query.strip(), "op": "WILDCARD"}]
    sort_key = NEXUS_COLLECTION_SORTS.get(sort, "endorsements")
    last = None
    for _ in range(10):
        q = ("query($filter: CollectionsSearchFilter, $sort: [CollectionsSearchSort!], $offset: Int, $count: Int) {"
             " collectionsV2(filter: $filter, sort: $sort, offset: $offset, count: $count) { totalCount nodes { %s } } }" % " ".join(fields))
        try:
            d = nexus_graphql(q, {"filter": flt, "sort": [{sort_key: {"direction": "DESC"}}], "offset": offset, "count": count}, key)
        except NexusError as ex:
            last = str(ex)
            m = re.search(r'Cannot query field "(\w+)"', last) or re.search(r"field '(\w+)'", last, re.I)
            if m:
                bad = next((f for f in fields if f.split()[0] == m.group(1) or f" {m.group(1)} " in f" {f} "), None)
                if bad and bad != "slug":
                    if bad.startswith("latestPublishedRevision") and m.group(1) != "latestPublishedRevision":
                        fields[fields.index(bad)] = bad.replace(" " + m.group(1), "")
                    else:
                        fields.remove(bad)
                    continue
            if sort_key != "endorsements" and sort_key in last:
                sort_key = "endorsements"
                continue
            raise
        c = d.get("collectionsV2") or {}
        nodes = c.get("nodes") or []
        out = []
        for n in nodes:
            rev = n.get("latestPublishedRevision") or {}
            if rev.get("adultContent") and not adult:
                continue
            out.append({"slug": n.get("slug"), "name": n.get("name"), "summary": n.get("summary") or "",
                        "author": (n.get("user") or {}).get("name"), "category": (n.get("category") or {}).get("name"),
                        "endorsements": n.get("endorsements"), "downloads": n.get("totalDownloads"), "updated": n.get("updatedAt"),
                        "picture": (n.get("tileImage") or {}).get("thumbnailUrl"), "revision": rev.get("revisionNumber"),
                        "mods": rev.get("modCount"), "size": rev.get("totalSize"), "adult": bool(rev.get("adultContent"))})
        total = c.get("totalCount")
        total = int(total) if isinstance(total, (int, float)) else None
        return {"total": total, "collections": out, "has_more": (offset + len(nodes) < total) if total is not None else len(nodes) >= count}
    raise NexusError(last or "search failed")

def nexus_list_v1(kind: str, key: str) -> dict:
    """Fallback lists from the v1 API: trending / latest_added / latest_updated."""
    path = {"trending": "trending", "new": "latest_added", "updated": "latest_updated"}.get(kind, "trending")
    data = nexus_get(f"/v1/games/{NEXUS_GAME}/mods/{path}.json", key)
    out = [{"mod_id": m.get("mod_id"), "name": m.get("name"), "summary": m.get("summary") or "", "version": m.get("version"),
            "author": m.get("author"), "picture": m.get("picture_url"), "endorsements": m.get("endorsement_count"),
            "downloads": m.get("mod_downloads"), "updated": m.get("updated_time"), "adult": m.get("contains_adult_content")}
           for m in data or [] if m.get("available", True)]
    return {"total": len(out), "mods": out, "source": "v1"}

def nexus_mod_files(mod: int, key: str) -> list[dict]:
    data = nexus_get(f"/v1/games/{NEXUS_GAME}/mods/{mod}/files.json", key)
    out = []
    for f in data.get("files", []):
        cat = str(f.get("category_name") or "").upper()
        if cat in ("ARCHIVED", "DELETED"):
            continue
        out.append({"file_id": f.get("file_id"), "name": f.get("name"), "version": f.get("version") or f.get("mod_version"),
                    "category": cat, "primary": bool(f.get("is_primary")), "size": int(f.get("size_in_bytes") or 0) or int(f.get("size_kb") or 0) * 1024,
                    "file_name": f.get("file_name"), "description": f.get("description") or "", "uploaded": f.get("uploaded_timestamp")})
    order = {"MAIN": 0, "UPDATE": 1, "OPTIONAL": 2, "MISCELLANEOUS": 3, "OLD_VERSION": 4}
    out.sort(key=lambda f: (order.get(f["category"], 5), not f["primary"], -(f["uploaded"] or 0)))
    return out

# --- Mod updates ------------------------------------------------------------------------------------
NEXUS_SOURCES = ".nexus_sources.json"   # in the archives folder: archive file name -> Nexus mod/file it came from
UPDATES_FILE = "updates.json"           # in the output folder: last check and what it found
UPDATE_FULL_DAYS = 30                   # a full re-check after this; in between only mods Nexus lists as updated
UPDATE_WORKERS = 4

def remember_nexus_source(archives_root: Path, fname: str, mod: int, file_id: int, info: dict | None = None) -> None:
    p = archives_root / NEXUS_SOURCES
    try:
        d = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        d = {}
    d[fname] = {"mod": int(mod), "file": int(file_id), "version": (info or {}).get("version") or (info or {}).get("mod_version"),
                "at": int(time.time())}
    try:
        p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass

def nexus_archive_ref(fname: str) -> dict | None:
    """Mod ID, version and upload time from a Nexus file name, both the classic
    "<Name>-<ModID>-<version>-<unix time>.ext" and the newer "<Name> <ModID> <version> <ISO time>Z <code>.ext"."""
    stem = re.sub(r"(\.tar)?\.[A-Za-z0-9]+$", "", FIRST_VOLUME_RE.sub(r"\1", fname))
    parts = stem.split("-")
    if len(parts) >= 3 and re.fullmatch(r"\d{9,11}", parts[-1]):
        for i in range(1, len(parts) - 1):
            if re.fullmatch(r"\d+", parts[i].strip()):
                return {"mod": int(parts[i]), "version": ".".join(p for p in parts[i + 1:-1] if p) or None, "ts": int(parts[-1])}
    m = re.match(r"^(.+)[ _-](\d+)[ _-](\S+?)[ _-](\d{4})-(\d{2})-(\d{2})T(\d{2})-(\d{2})(?:-(\d{2}))?Z(?:[ _-][0-9A-Za-z]+)?$", stem)
    if m:
        y, mo, d, h, mi, sec = (int(x or 0) for x in m.group(4, 5, 6, 7, 8, 9))
        ts = int(datetime(y, mo, d, h, mi, sec, tzinfo=timezone.utc).timestamp())
        return {"mod": int(m.group(2)), "version": m.group(3).replace("-", "."), "ts": ts, "approx": True}
    return None

def installed_nexus_archives(output_root: Path, archives_root: Path) -> list[dict]:
    """Unpacked archives that come from Nexus: {archive, name, folder, mod, file?, version, ts}."""
    try:
        state = json.loads((output_root / "processed_archives.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    try:
        src = json.loads((archives_root / NEXUS_SOURCES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        src = {}
    out = []
    for rel, rec in state.items():
        dest = rec.get("destination")
        if not dest or rec.get("removed") or not Path(dest).is_dir():
            continue
        name = Path(rel).name
        ref = nexus_archive_ref(name) or {}
        known = src.get(name) or {}
        if not (ref.get("mod") or known.get("mod")):
            continue
        out.append({"archive": rel, "name": name, "folder": Path(dest).name, "mod": int(known.get("mod") or ref["mod"]),
                    "file": known.get("file"), "version": known.get("version") or ref.get("version"),
                    "ts": ref.get("ts"), "approx": ref.get("approx", False)})
    return out

def nexus_files_compact(mod: int, key: str) -> dict:
    data = nexus_get(f"/v1/games/{NEXUS_GAME}/mods/{mod}/files.json", key)
    files = [{"id": int(f["file_id"]), "name": f.get("name") or "", "version": f.get("version") or f.get("mod_version") or "",
              "cat": str(f.get("category_name") or "").upper(), "fname": f.get("file_name") or "",
              "up": int(f.get("uploaded_timestamp") or 0), "size": int(f.get("size_in_bytes") or 0) or int(f.get("size_kb") or 0) * 1024}
             for f in (data or {}).get("files", []) if f.get("file_id")]
    ups = [[int(u["old_file_id"]), int(u["new_file_id"])] for u in (data or {}).get("file_updates", [])
           if u.get("old_file_id") and u.get("new_file_id")]
    return {"files": files, "updates": ups, "at": int(time.time())}

def evaluate_update(a: dict, info: dict, have_names: set[str]) -> dict:
    """Compare one installed archive with the mod's file list on Nexus."""
    files = {f["id"]: f for f in info.get("files", [])}
    cur = files.get(int(a["file"])) if a.get("file") else None
    if cur is None:
        cur = next((f for f in files.values() if f["fname"] and f["fname"].lower() == a["name"].lower()), None)
    if cur is None and a.get("ts"):
        tol = 90 if a.get("approx") else 2
        cur = next((f for f in files.values() if f["up"] and abs(f["up"] - a["ts"]) <= tol), None)
    if cur is None:
        return {"status": "unknown"}
    res = {"status": "ok", "file": cur["id"], "cur_version": cur["version"] or a.get("version"), "cur_name": cur["name"]}
    nxt = dict((o, n) for o, n in info.get("updates", []))
    tgt, seen = cur["id"], {cur["id"]}
    while tgt in nxt and nxt[tgt] not in seen:  # follow "this file was replaced by that one"
        tgt = nxt[tgt]
        seen.add(tgt)
    target = files.get(tgt) if tgt != cur["id"] else None
    guessed = False
    if target is None and cur["cat"] in ("OLD_VERSION", "ARCHIVED", "DELETED"):
        cands = [f for f in files.values() if f["cat"] in ("MAIN", "UPDATE", "OPTIONAL") and f["name"] == cur["name"] and f["up"] > cur["up"]]
        if not cands:
            cands = [f for f in files.values() if f["cat"] == "MAIN" and f["up"] > cur["up"]] if cur["cat"] != "OPTIONAL" else []
        target = max(cands, key=lambda f: f["up"], default=None)
        guessed = target is not None
        if target is None:
            res["status"] = "outdated"
            return res
    if target is None:
        return res
    res.update(status="update", target=target["id"], version=target["version"], target_name=target["name"],
               target_fname=target["fname"], size=target["size"], guessed=guessed)
    if target["fname"] and target["fname"].lower() in have_names:
        res["status"] = "downloaded"  # the new archive is already in the archives folder: unpack to apply
    return res

def load_updates(output_root: Path) -> dict:
    try:
        d = json.loads((output_root / UPDATES_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}

def check_updates(output_root: Path, archives_root: Path, key: str, full: bool = False) -> dict:
    """Ask Nexus which installed mods have newer files. The first check (and one a month) reads every
    mod's file list; in between only mods that Nexus reports as updated since the last check."""
    from concurrent.futures import ThreadPoolExecutor
    data = load_updates(output_root)
    mods_info: dict = data.get("mods") or {}
    arcs = installed_nexus_archives(output_root, archives_root)
    ids = sorted({a["mod"] for a in arcs})
    now = int(time.time())
    print(tr("\n=== ПЕРЕВІРКА ОНОВЛЕНЬ ==="))
    print(tr("Архівів з Nexus: {0} (модів: {1})", len(arcs), len(ids)))
    last_full, last = int(data.get("full_at") or 0), int(data.get("checked_at") or 0)
    if full or not last_full or now - last_full > UPDATE_FULL_DAYS * 86400:
        need = set(ids)
        data["full_at"] = now
        print(tr("Повна перевірка: список файлів кожного мода ({0} запит(ів) до Nexus)...", len(need)), flush=True)
    else:
        age = now - last
        period = "1d" if age < 86400 else "1w" if age < 7 * 86400 else "1m"
        upd = nexus_get(f"/v1/games/{NEXUS_GAME}/mods/updated.json", key, {"period": period}) or []
        changed = {int(x["mod_id"]) for x in upd if x.get("mod_id") and int(x.get("latest_file_update") or now) >= last - 3600}
        need = (set(ids) & changed) | {i for i in ids if str(i) not in mods_info}
        print(tr("Оновлені на Nexus з останньої перевірки: {0}; з них у тебе: {1}", len(changed), len(need)), flush=True)
    errors: list[str] = []
    done = 0

    def one(mid: int):
        try:
            return mid, nexus_files_compact(mid, key), None
        except NexusError as ex:
            return mid, None, str(ex)
    if need:
        with ThreadPoolExecutor(max_workers=UPDATE_WORKERS) as pool:
            for mid, info, err in pool.map(one, sorted(need)):
                done += 1
                if done % 50 == 0:
                    print(tr("  перевірено {0}/{1}...", done, len(need)), flush=True)
                if info is not None:
                    mods_info[str(mid)] = info
                elif err:
                    errors.append(f"{mid}: {err}")
                    if "401" in err or "API Key" in err:
                        break
    have = {p.name.lower() for p in archives_root.iterdir() if p.is_file()} if archives_root.is_dir() else set()
    results = {}
    for a in arcs:
        info = mods_info.get(str(a["mod"]))
        r = evaluate_update(a, info, have) if info else {"status": "unchecked"}
        r.update(mod=a["mod"], folder=a["folder"], archive=a["archive"])
        results[a["archive"]] = r
    data.update(mods={k: v for k, v in mods_info.items() if int(k) in set(ids)}, checked_at=now, results=results)
    (output_root / UPDATES_FILE).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print_updates(results)
    for e_ in errors[:10]:
        print(tr("  ПОМИЛКА Nexus: {0}", e_))
    return data

def print_updates(results: dict) -> None:
    ups = [r for r in results.values() if r["status"] in ("update", "downloaded")]
    old = [r for r in results.values() if r["status"] == "outdated"]
    unk = [r for r in results.values() if r["status"] == "unknown"]
    if not ups:
        print(tr("Нових версій немає."))
    else:
        print(tr("Є нові версії: {0}", len(ups)))
        for r in sorted(ups, key=lambda r: r["folder"].lower()):
            print(f"  {r['folder']}: {r.get('cur_version') or '?'} → {r.get('version') or '?'}"
                  + (tr("  (архів уже завантажено — розпакуй, щоб застосувати)") if r["status"] == "downloaded" else "")
                  + (tr("  (новий файл визначено за назвою — перевір)") if r.get("guessed") else ""))
        print(tr("Оновити: --update-mod НАЗВА або --update-all (з Premium файли качаються самі)"))
    if old:
        print(tr("Файл застарів, а заміни автор не вказав: {0}", ", ".join(sorted(r["folder"] for r in old))))
    if unk:
        print(tr("Не знайдено на Nexus (файл видалено або архів перейменовано): {0}", len(unk)))

REDEPLOY_FILE = "redeploy_needed.json"  # mods whose files changed after the last deployment (updated in place)

def mark_redeploy(output_root: Path, name: str) -> None:
    rp = output_root / REDEPLOY_FILE
    try:
        names = set(json.loads(rp.read_text(encoding="utf-8"))) if rp.exists() else set()
    except (OSError, ValueError):
        names = set()
    names.add(name)
    rp.write_text(json.dumps(sorted(names), ensure_ascii=False), encoding="utf-8")

def redeploy_needed(output_root: Path) -> set[str]:
    try:
        return set(json.loads((output_root / REDEPLOY_FILE).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()

def archive_update_of(output_root: Path, state: dict, archive: Path, destination: Path) -> str | None:
    """The state key of the archive that `archive` replaces. Only when the last update check found
    exactly this file as the new version of the archive in this mod folder: a second file of the same
    mod with a similar name (a patch, an optional part) must never replace the main one."""
    for r in (load_updates(output_root).get("results") or {}).values():
        if r.get("status") not in ("update", "downloaded") or r.get("folder") != destination.name:
            continue
        if (r.get("target_fname") or "").lower() != archive.name.lower():
            continue
        rec = state.get(r.get("archive"))
        if rec and not rec.get("removed") and rec.get("destination") and Path(rec["destination"]).name == destination.name \
                and Path(r["archive"]).name != archive.name:
            return r["archive"]
    return None

def finish_mod_update(output_root: Path, mods_root: Path, archives_root: Path, state: dict, old_rel: str, destination: Path) -> None:
    """Swap in the new version: old folder and old archive to the trash, the new folder takes the same
    name (so order, enabled state and profiles stay), and the mod is marked for redeployment."""
    how = send_to_trash(destination)
    print(tr("  стара версія: папка {0}", tr("у кошику") if how == "trash" else tr("видалена")))
    ap = Path(old_rel) if Path(old_rel).is_absolute() else archives_root / old_rel
    if not Path(old_rel).is_absolute():
        for v in archive_volumes(ap) if ap.exists() else []:
            how = send_to_trash(v)
            print(tr("  старий архів {0}: {1}", v.name, tr("у кошику") if how == "trash" else tr("видалено")))
    state.pop(old_rel, None)
    mark_redeploy(output_root, destination.name)
    up = load_updates(output_root)
    if up.get("results"):  # this one is done: drop it from the found updates
        up["results"] = {k: v for k, v in up["results"].items() if v.get("folder") != destination.name}
        (output_root / UPDATES_FILE).write_text(json.dumps(up, ensure_ascii=False), encoding="utf-8")

def update_target(output_root: Path, folder: str) -> dict | None:
    for r in (load_updates(output_root).get("results") or {}).values():
        if r.get("folder") == folder and r.get("status") in ("update", "downloaded"):
            return r
    return None

def nexus_mm_url(mod: int, file_id: int | None = None) -> str:
    """The site page whose "Mod Manager Download" button sends an nxm:// link to this program."""
    return (f"https://www.nexusmods.com/{NEXUS_GAME}/mods/{mod}?tab=files&file_id={file_id}&nmm=1" if file_id
            else f"https://www.nexusmods.com/{NEXUS_GAME}/mods/{mod}?tab=files")

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

def skse_ensure(game_dir: Path, mods_root: Path, archives_root: Path, output_root: Path) -> int:
    """For the one-button setup: SKSE is installed when a mod needs it and it is missing — downloaded
    with a Premium key, otherwise a clear warning (the setup goes on without it)."""
    runtime = skse_target_runtime(game_dir, mods_root)
    want = "skse64_{}_{}_{}.dll".format(*runtime) if runtime else None
    try:
        mods = json.loads((output_root / "manifest.json").read_text(encoding="utf-8")).get("mods", [])
    except Exception:
        mods = []
    needing = [m["name"] for m in mods if m.get("skse_plugins")]
    if not needing:  # FOMOD sources (not installed yet) and DLLs the static check could not read: the file lists
        rx = re.compile(r"(^|/)skse/plugins/[^/\t]+\.dll\t", re.IGNORECASE)
        for m in mods:
            if m.get("layout") == "fomod":  # FOMOD sources have no file list: look at their DLLs directly
                d = mods_root / m["name"]
                if d.is_dir() and any(re.search(r"(^|/)skse/plugins/[^/]+$", p.relative_to(d).as_posix(), re.I)
                                      for p in d.rglob("*") if p.suffix.lower() == ".dll"):
                    needing.append(m["name"])
                continue
            idx = output_root / "index" / str(m.get("index") or "")
            if m.get("index") and idx.is_file():
                try:
                    with gzip.open(idx, "rt", encoding="utf-8", errors="replace") as f:
                        if any(rx.search(line.replace("\\", "/")) for line in f):
                            needing.append(m["name"])
                except OSError:
                    pass
    have = bool(want) and (ci_join(game_dir, want) is not None or any(
        p.name.lower() == want for p in list(mods_root.glob("*/*")) + list(mods_root.glob("*/*/*"))))
    print(tr("\n=== SKSE ==="))
    if have:
        print(tr("SKSE для гри {0} є.", ".".join(map(str, runtime))))
        return 0
    if not needing:
        print(tr("SKSE не знайдено, але жоден мод його не потребує — пропускаю."))
        return 0
    acc = nexus_account()
    if not acc:
        print(tr("УВАГА: SKSE не встановлено, а його потребують моди ({0}). Увійди в Nexus (Головна), щоб програма встановила його сама, або встанови SKSE вручну.", len(needing)))
        return 0
    rc = skse_install(game_dir, mods_root, archives_root)
    if rc:
        print(tr("УВАГА: SKSE встановити не вдалося — моди з SKSE-плагінами не працюватимуть."))
    return 0

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

# Lines the GUI marks as problems (both languages of the engine's output).
GUI_PROBLEM_RE = re.compile(r"^(?!\s*ERRORS?:\s*$).*(ERROR|ПОМИЛКА|CRITICAL|КРИТИЧНО|Traceback|NOT activated|НЕ активовано|FAIL|"
                            r"Could not read contents|Не вдалося прочитати вміст)")
GUI_WARN_RE = re.compile(r"WARNING|УВАГА|⚠")

GUI_LOG_MAX = 50 * 1024 * 1024   # the RAM log never grows past this...
GUI_LOG_KEEP = 40 * 1024 * 1024  # ...when it does, only the newest lines (this much) are kept

def gui_session_log_path() -> Path:
    """The whole console text of this GUI session, in RAM (tmpfs) so it costs no disk writes."""
    for base in (os.environ.get("XDG_RUNTIME_DIR"), "/dev/shm"):
        if base and os.path.isdir(base) and os.access(base, os.W_OK):
            return Path(base) / f"skyrim-mod-engine-{os.getuid()}" / "session.log"
    return Path(tempfile.gettempdir()) / f"skyrim-mod-engine-{os.getuid()}" / "session.log"

def shlex_quote(a: str) -> str:
    import shlex
    return shlex.quote(a)

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
    "scan_suffix": ["--yes", "--collision", "suffix"],
    # "auto_all" (the one button) is a chain of three runs, see GuiApp.auto_all
    "skse_launcher": ["--skse-launcher"],
    "skse_launcher_restore": ["--skse-launcher-restore"],
    "loot_on": ["--loot", "on"],
    "loot_off": ["--loot", "off"],
    "check_updates": ["--check-updates"],
    "check_updates_full": ["--check-updates", "full"],
    "update_all": ["--update-all"],
    "papyrus_on": ["--papyrus-log", "on"],
    "papyrus_off": ["--papyrus-log", "off"],
    "collection_none": ["--collection", "none", "--scan-only"],
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
    "set_game": ([], "--set-game"), "set_skse_log": ([], "--set-skse-log"),
    "remove_mod": (["--scan-only"], "--remove-mod"), "remove_mod_keep": (["--keep-archive", "--scan-only"], "--remove-mod"),
    "nxm": ([], "--nxm"), "collection_set": (["--scan-only"], "--collection"),
    "plugin_order": (["--scan-only"], "--plugin-order"),
    "update_mod": ([], "--update-mod"),
}
# Console hints in the event log -> where the same thing is in the GUI: flag -> (page key, button key).
GUI_HINTS = [
    (r'--game\s+"[^"]*"', "settings", "gameDir"), (r"--game-repair", "backups", "gcRepair"),
    (r"--game-baseline", "backups", "gcBaseline"), (r"--game-check", "backups", "gcCheck"),
    (r"--skse-launcher-restore", "deploy", "launcherRestore"), (r"--skse-launcher", "deploy", "launcherSet"),
    (r"--skse-install", "deploy", "skseInstall"), (r"--nxm-handler", "settings", "nxHandler"),
    (r"--nexus-login", "settings", "nxLogin"), (r"--fomod-redo", "fomod", "fmChoose"), (r"--fomod", "fomod", "installFomod"),
    (r"--undeploy", "deploy", "undeployBtn"), (r"--deploy", "deploy", "deployBtn"), (r"--conflicts", "conflicts", "recompute"),
    (r"--incremental-backup", "backups", "incOn"), (r"--skse(?=[^\w-]|$)", "deploy", "skseBtn"),
    (r"--set-game", "settings", "gameDir"), (r"--set-skse-log", "settings", "skseLog"),
    (r"--update-mod(?: НАЗВА| NAME)?", "mods", "updBtn"), (r"--update-all", "mods", "updAll"), (r"--check-updates", "mods", "updCheck"),
]
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
        self.plugin_hdr: dict[str, tuple] = {}  # path -> (mtime_ns, size, master flag, light, masters)
        self.lock = threading.Lock()
        self.job: dict | None = None
        self.log: list[str] = []
        self.log_base = 0
        self.last_beat = 0.0
        self.started = time.time()
        self.stop = threading.Event()
        self.proc: subprocess.Popen | None = None
        self.question: dict | None = None  # an open FOMOD question from the running job
        self.qseq = 0
        self.partial = ""   # output line still being written (a prompt waiting for an answer)
        self.chain: list = []      # the next runs of the one-button setup
        self.chain_warn: list = []
        self.notice: dict | None = None  # "ready to play" / "failed" after the one-button setup
        self.notice_seq = 0
        self._answered = ""  # that prompt, once answered in the terminal: already logged together with the answer
        self.thumbs = self.out / "cache" / "thumbs"
        self.uploads: dict = {}  # web upload progress, name -> bytes received
        self.logfile = gui_session_log_path()
        try:
            self.logfile.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self.logfile.parent, 0o700)
            self._logf = self.logfile.open("w", encoding="utf-8")  # every start begins with a clean log
            self._logf.write(f"===== Skyrim Mod Engine {APP_VERSION} · {datetime.now().astimezone().isoformat(timespec='seconds')} =====\n")
            self._logf.flush()
        except OSError:
            self._logf = None
        self._i18n = None
        engine.load_saved_paths(self.out)

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

    def plugin_info(self, gdata: Path | None, name: str) -> dict:
        e = self.e
        p = e.ci_join(gdata, name) if gdata else None
        rec = {"n": name, "m": name.lower().endswith((".esm", ".esl")), "l": name.lower().endswith(".esl"), "ms": []}
        if p is None:
            rec["miss"] = True
            return rec
        try:
            st = p.stat()
        except OSError:
            rec["miss"] = True
            return rec
        c = self.plugin_hdr.get(str(p))
        if not c or c[0] != st.st_mtime_ns or c[1] != st.st_size:
            c = (st.st_mtime_ns, st.st_size, e.plugin_master_flag(name, p), e.is_light_plugin(name, p),
                 e.read_plugin_masters(p) or [])
            self.plugin_hdr[str(p)] = c
        rec.update(m=c[2], l=c[3], ms=c[4])
        return rec

    def plugin_order_state(self, journal: dict | None, profile: str, pdir: Path, gdir: Path | None) -> dict:
        """What the plugins page needs: the deployed order with flags, active state and the user's order."""
        e = self.e
        uo = e.load_user_plugin_order(pdir)
        out = {"custom": bool(uo), "items": [], "same_profile": False}
        if not journal or not journal.get("plugins"):
            return out
        out["same_profile"] = journal.get("profile", e.DEFAULT_PROFILE) == profile
        gd = Path(journal["game_dir"]) if journal.get("game_dir") else gdir
        gdata = e.ci_join(gd, "Data") if gd else None
        active = None
        pt = e.plugins_txt_path(gd) if gd else None
        if pt and pt.exists():
            try:
                active = {l[1:].strip().lower() for l in pt.read_text(encoding="utf-8", errors="replace").splitlines()
                          if l.startswith("*")}
            except OSError:
                active = None
        off = {x.lower() for x in journal.get("plugins_disabled") or []}
        for name in journal["plugins"]:
            r = self.plugin_info(gdata, name)
            r["on"] = (name.lower() in active) if active is not None else name.lower() not in off
            out["items"].append(r)
        if uo:  # saved, but not what plugins.txt has now (e.g. the profile is not the deployed one)
            want = [x.lower() for x in uo["order"]]
            have = [x["n"].lower() for x in out["items"] if x["n"].lower() in set(want)]
            out["applied"] = out["same_profile"] and have == [x for x in want if x in set(have)] and \
                all(x["on"] != (x["n"].lower() in {d.lower() for d in uo["disabled"]}) for x in out["items"] if x["n"].lower() in set(want))
        return out

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
                ref = e.nexus_archive_ref(Path(rel).name) or {}
                meta[Path(dest).name] = {"nexus_id": ref.get("mod"), "version": ref.get("version"), "archive": Path(rel).name}
        upd = e.load_updates(self.out)
        upd_by: dict[str, dict] = {}
        for r in (upd.get("results") or {}).values():
            if r.get("status") in ("update", "downloaded", "outdated"):
                upd_by[r["folder"]] = {k: r.get(k) for k in ("status", "version", "cur_version", "mod", "target", "guessed", "target_name", "size")}
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
                "update": upd_by.get(base or n),
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
                pending = jorder != [m["name"] for m in mods if m["enabled"]] or \
                    bool(e.redeploy_needed(self.out) & {m["name"] for m in mods if m["enabled"]})
        ow = pdir / "overwrite"
        ow_files = sorted(p.relative_to(ow).as_posix() for p in ow.rglob("*") if p.is_file()) if ow.is_dir() else []
        bdir = self.out / "backup" / "files"
        backups = sum(1 for p in bdir.rglob("*") if p.is_file()) if bdir.is_dir() else 0
        changed_dir = self.out / "deploy_changed"
        changed = sorted((d.name for d in changed_dir.iterdir() if d.is_dir()), reverse=True) if changed_dir.is_dir() else []
        skse = self.read_json("skse_report.json")
        e.load_saved_paths(self.out)
        settings = e.read_settings(self.out)
        gdir = e.find_game_dir(None)
        exe = e.ci_join(gdir, "SkyrimSE.exe") if gdir else None
        try:
            exe_v = ".".join(map(str, e.pe_file_version(exe))) if exe else None
        except Exception:
            exe_v = None
        srs = e.find_srs(gdir, e.configured_dirs(self.root, self.out)[1]) if gdir else None
        collisions = [Path(a.get("archive") or a.get("path") or a.get("name") or "").name
                      for a in manifest.get("archives", []) if a.get("status") == "skipped-name-collision"]
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
            "updates": {"checked_at": upd.get("checked_at"), "count": sum(1 for m in mods if m.get("update") and m["update"]["status"] != "outdated")},
            "deployed": deployed, "pending": pending, "log_alert": self.crash_alert(gdir), "plugin_order": self.plugin_order_state(journal, profile, pdir, gdir),
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
            "game": {"dir": str(gdir) if gdir else None, "saved": settings.get("game_dir"), "exe_version": exe_v,
                     # Skyrim Runtime Swapper: the Steam game stays new, SKSE starts on the older runtime
                     "srs": {"source": str(srs.get("sourceVersion")), "target": str(srs.get("targetVersion")),
                             "deployed": bool(srs.get("_deployed"))} if srs else None,
                     "skse_log": settings.get("skse_log"), "loot": settings.get("loot") is not False,
                     "launcher": (self.out / e.LAUNCHER_JOURNAL).exists()},
            "collisions": collisions, "drop_extract": settings.get("drop_mode") == "extract",
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
    def auto_all(self) -> str | None:
        """One button: 1) unpack new archives, 2) SKSE when a mod needs it (downloaded with Premium, else a
        warning) and unpack it, 3) FOMODs with automatic choices and deployment. The game is not started."""
        if self.job and self.job.get("running"):
            return "busy"
        steps = [("auto_1", ["--yes"]), ("auto_2", ["--skse-ensure", "--yes"]), ("auto_3", ["--fomod", "--auto", "--deploy"])]
        for a, _ in steps:
            GUI_ACTIONS[a] = []
        self.chain, self.chain_warn = steps[1:], []
        return self.start(*steps[0])

    def _chain_step_done(self, action: str, rc: int) -> None:
        with self.lock:
            lines = list(self.log)
        for l in lines:
            t_ = l.strip()
            if (t_.startswith(("УВАГА", "WARNING")) or GUI_PROBLEM_RE.search(t_)) and t_ not in self.chain_warn:
                self.chain_warn.append(t_)
        if rc == 0 and self.chain:
            nxt = self.chain.pop(0)
            if self.start(*nxt) is None:
                return
        self.chain = []
        ok = rc == 0 and action == "auto_3" and not self._deploy_failed()
        self.notice_seq += 1
        no_skse = any("SKSE" in w and w.startswith(("УВАГА", "WARNING")) for w in self.chain_warn)
        self.notice = {"id": self.notice_seq, "ok": ok, "no_skse": no_skse, "warnings": self.chain_warn[:8], "more": max(0, len(self.chain_warn) - 8)}
        self._append(self.e.tr("✔ Готово — можна грати.") if ok else self.e.tr("✖ Автоналаштування не завершено — дивись журнал вище."))

    def start(self, action: str, extra: list[str] | None = None, interactive: bool = False, term: bool = False) -> str | None:
        if action == "auto_all":
            return self.auto_all()
        with self.lock:
            if self.job and self.job.get("running"):
                return "busy"
            args = list(GUI_ACTIONS.get(action, [])) + list(extra or [])
            if not args:
                return "unknown action"
            self.job = {"action": action, "args": args, "running": True, "rc": None, "started": time.time()}
            self.log_base += len(self.log)
            self.log = []
            self.question = None
            self.job["term"] = term
            self.partial = ""
        threading.Thread(target=self._run, args=(action, args, interactive, term), daemon=True).start()
        return None

    def _line(self, line: str) -> None:
        if line.startswith("@@FOMOD@@ "):
            try:
                q = json.loads(line[10:])
            except ValueError:
                return
            with self.lock:
                self.qseq += 1
                q["id"] = self.qseq
                self.question = q
            return
        self._append(line)

    def _run(self, action: str, args: list[str], interactive: bool = False, term: bool = False) -> None:
        import codecs
        env = dict(os.environ, PYTHONUNBUFFERED="1", SME_GUI="1")
        if interactive:
            env["SME_FOMOD_IO"] = "1"  # FOMOD questions come as @@FOMOD@@ JSON lines, answers go to stdin
        if term:
            env["SME_TERM"] = "1"  # typed in the GUI terminal: prompts are answered there
        cmd = [sys.executable, str(self.engine_path)] + args
        self._append("$ " + " ".join(["python3", self.engine_path.name] + [shlex_quote(a) for a in args]))
        try:
            proc = subprocess.Popen(cmd, cwd=self.root, stdin=subprocess.PIPE if (interactive or term) else subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
            self.proc = proc
            # Read raw chunks, not lines: a prompt ("[y/N]: ") has no newline and must still be seen.
            dec = codecs.getincrementaldecoder("utf-8")("replace")
            buf = ""
            while True:
                chunk = proc.stdout.read1(65536)
                if not chunk:
                    break
                buf += dec.decode(chunk)
                with self.lock:
                    if self._answered and buf.startswith(self._answered):
                        rest = buf[len(self._answered):]
                        buf = rest[1:] if rest.startswith("\n") else rest
                    self._answered = ""
                *lines, buf = buf.split("\n")
                for line in lines:
                    self._line(line.rstrip("\r").split("\r")[-1])  # "\r" redraws a progress line
                with self.lock:
                    self.partial = buf.split("\r")[-1]
            buf += dec.decode(b"", final=True)
            if buf:
                self._line(buf.split("\r")[-1])
            rc = proc.wait()
        except Exception as ex:
            self._append(f"ERROR: {ex}")
            rc = -1
        with self.lock:
            self.partial = ""
            self.question = None
            self.proc = None
            self.job.update(running=False, rc=rc, finished=time.time())
        if action == "deploy_launch" and rc == 0 and not self._deploy_failed():
            self.launch()
        if action.startswith("auto_"):
            self._chain_step_done(action, rc)
        if action == "drop_extract":
            inc = (self.out / "incoming").resolve()
            for i, a in enumerate(args):
                if a == "--extract" and i + 1 < len(args) and Path(args[i + 1]).resolve().parent == inc:
                    Path(args[i + 1]).unlink(missing_ok=True)  # the page's temporary upload, unpacked now
        if action in ("nxm", "import", "skse_install") and rc == 0:
            self.start("scan")  # a downloaded / added archive (SKSE too) is unpacked right away

    def _deploy_failed(self) -> bool:
        j = self.e.load_journal(self.out)
        return not (j and j.get("complete"))

    def gui_hint(self, line: str) -> str:
        """«--game-repair» means nothing to someone using the GUI: name the page and button instead."""
        if "--" not in line or line.startswith("$ "):
            return line
        if self._i18n is None:
            try:
                self._i18n = gui_i18n()
            except Exception:
                self._i18n = {}
        tab = self._i18n.get(self.e._LANG) or self._i18n.get("uk") or {}
        for pat, page, key in GUI_HINTS:
            if page in tab and key in tab:
                line = re.sub(r"(?<![\w-])" + pat + ("" if pat.endswith('"') else r"(?![\w-])"), f"«{tab[page]} → {tab[key]}»", line)
        return line

    def _append(self, line: str) -> None:
        line = self.gui_hint(line)
        with self.lock:
            if self._logf:  # every line, untrimmed: the full text stays readable however long the job was
                try:
                    if self._logf.tell() > GUI_LOG_MAX:
                        self._trim_logfile()
                    self._logf.write(line + "\n")
                    self._logf.flush()
                except OSError:
                    pass
            self.log.append(line)
            if len(self.log) > 8000:
                cut = len(self.log) - 6000
                self.log = self.log[cut:]
                self.log_base += cut

    def log_since(self, n: int) -> dict:
        with self.lock:
            start = max(0, n - self.log_base)
            return {"lines": self.log[start:], "next": self.log_base + len(self.log), "partial": self.partial, "notice": self.notice,
                    "job": dict(self.job) if self.job else None, "question": self.question}

    # ------------------------------------------------------------------ a real terminal (Konsole and others)
    def shell_rc(self) -> Path:
        """bash startup file: the user's own ~/.bashrc, then `sme` = this engine, in the engine's folder."""
        rc = self.out / "cache" / "sme_shell.rc"
        rc.parent.mkdir(parents=True, exist_ok=True)
        q = shlex_quote
        rc.write_text(
            '[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"\n'
            f"alias sme={q(q(sys.executable) + ' ' + q(str(self.engine_path)))}\n"
            f"cd {q(str(self.root))}\n"
            f"echo {q('Skyrim Mod Engine: sme --help  (sme = python3 ' + self.engine_path.name + ')')}\n", encoding="utf-8")
        return rc

    def shell_argv(self) -> list[str]:
        bash = shutil.which("bash")
        return [bash, "--rcfile", str(self.shell_rc()), "-i"] if bash else [os.environ.get("SHELL") or "/bin/sh"]

    def open_terminal(self) -> str:
        """Open the desktop's terminal (Konsole on the Steam Deck) in the engine's folder, with `sme` ready."""
        sh = self.shell_argv()
        cands = [("konsole", ["--workdir", str(self.root), "-e"] + sh), ("gnome-terminal", [f"--working-directory={self.root}", "--"] + sh),
                 ("xfce4-terminal", [f"--working-directory={self.root}", "-x"] + sh), ("kitty", ["--directory", str(self.root)] + sh),
                 ("alacritty", ["--working-directory", str(self.root), "-e"] + sh), ("x-terminal-emulator", ["-e"] + sh), ("xterm", ["-e"] + sh)]
        for name, args in cands:
            exe = shutil.which(name)
            if exe:
                try:
                    subprocess.Popen([exe] + args, cwd=self.root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, start_new_session=True)
                    return name
                except OSError:
                    continue
        return "none"

    # ------------------------------------------------------------------ the session log file
    def _trim_logfile(self) -> None:
        """Keep only the newest GUI_LOG_KEEP bytes (whole lines): RAM is not eaten by a long session."""
        self._logf.close()
        with self.logfile.open("rb") as f:
            f.seek(max(0, self.logfile.stat().st_size - GUI_LOG_KEEP))
            tail = f.read()
        nl = tail.find(b"\n")
        tail = tail[nl + 1:] if nl >= 0 else tail
        tmp = self.logfile.with_suffix(".tmp")
        tmp.write_bytes(b"[... older lines removed: the log is limited to the newest part ...]\n" + tail)
        tmp.replace(self.logfile)
        self._logf = self.logfile.open("a", encoding="utf-8")

    def log_read(self, start: int = 0, limit: int = 4 * 1024 * 1024, upto: int | None = None) -> dict:
        """A piece of the log file. size and log_next are taken together under the lock, so the page knows
        exactly which lines the file already holds and does not show them twice."""
        try:
            with self.lock:
                size = self.logfile.stat().st_size
                log_next = self.log_base + len(self.log)
            if upto is not None:
                size = min(size, upto)
            with self.logfile.open("rb") as f:
                f.seek(max(0, min(start, size)))
                data = f.read(max(0, min(limit, size - start)))
        except OSError:
            return {"text": "", "next": 0, "size": 0}
        cut = data.rfind(b"\n") + 1 if start + len(data) < size else len(data)  # whole lines only
        return {"text": data[:cut].decode("utf-8", "replace"), "next": start + cut, "size": size,
                "log_next": log_next, "path": str(self.logfile)}

    def log_save(self) -> str | None:
        """A copy of the session log next to the other reports (the RAM copy is gone after a reboot)."""
        d = self.out / "logs"
        try:
            d.mkdir(parents=True, exist_ok=True)
            dest = d / f"sme-{datetime.now().strftime('%Y%m%d-%H%M%S')}.log"
            shutil.copyfile(self.logfile, dest)
            self._diag_paths = getattr(self, "_diag_paths", set()) | {str(d)}
            return str(dest)
        except OSError:
            return None

    def log_open(self) -> str:
        opener = shutil.which("xdg-open")
        if not opener or not self.logfile.exists():
            return "no-opener" if not opener else "bad"
        subprocess.Popen([opener, str(self.logfile)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return "ok"

    # ------------------------------------------------------------------ terminal (the engine's own CLI)
    def term_run(self, cmdline: str) -> str | None:
        """A command typed in the GUI terminal: options of this engine only, not a system shell."""
        import shlex
        try:
            args = shlex.split(cmdline)
        except ValueError as ex:
            return str(ex)
        if args and Path(args[0]).name.startswith("python"):
            args = args[1:]
        if args and (args[0].endswith(".py") or args[0] in ("sme", "mod_engine")):
            args = args[1:]
        if not args:
            args = ["--help"]
        if not all(a.startswith("-") or i > 0 for i, a in enumerate(args)):
            return "options-only"
        if any(a == "--gui" or a.startswith("--gui=") for a in args):
            return "no-gui"
        GUI_ACTIONS["term"] = []
        return self.start("term", args, term=True)

    def term_input(self, text: str) -> str | None:
        """A line typed while a terminal command runs: goes to its input, shown after the prompt."""
        with self.lock:
            j, proc = self.job, self.proc
            if not (j and j.get("running") and j.get("term") and proc and proc.stdin):
                return "no-job"
            prompt, self.partial = self.partial, ""
            self._answered = prompt
        self._append(prompt + text)
        try:
            proc.stdin.write((text + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (OSError, ValueError):
            return "job ended"
        return None

    def term_stop(self) -> str | None:
        """Ctrl+C for the terminal command (the second press ends it)."""
        import signal
        with self.lock:
            j, proc = self.job, self.proc
        if not (j and j.get("running") and j.get("term") and proc):
            return "no-job"
        try:
            if j.get("stopping"):
                proc.terminate()
            else:
                j["stopping"] = True
                proc.send_signal(signal.SIGINT)
        except OSError:
            pass
        return None

    # ------------------------------------------------------------------ diagnostics
    def diagnostics(self, gui: str = "") -> list[dict]:
        """Everything a player (or a bug report) needs: paths, versions, states. Sections of rows
        {k: label key, v: value, st: ok|warn|bad|info, path: folder/file to open}. Nothing is hashed here."""
        e = self.e
        e.load_saved_paths(self.out)
        st = e.read_settings(self.out)
        a_dir, m_dir = e.configured_dirs(self.root, self.out)
        gd = e.find_game_dir(None)
        out: list[dict] = []

        def sec(key, rows):
            out.append({"key": key, "rows": [r for r in rows if r]})

        def row(k, v, ok=None, path=None, warn=False):
            stt = "info" if ok is None else ("ok" if ok else ("warn" if warn else "bad"))
            return {"k": k, "v": "—" if v in (None, "") else str(v), "st": stt, "path": str(path) if path else None}

        def mtime(p):
            try:
                return datetime.fromtimestamp(p.stat().st_mtime).astimezone().isoformat(timespec="seconds")
            except OSError:
                return None

        # SME itself
        sec("dgSme", [row("dgVersion", e.APP_VERSION), row("dgScript", self.engine_path, path=self.engine_path.parent),
                      row("dgPython", f"{sys.version.split()[0]} ({sys.executable})"),
                      row("dgGui", gui or "?"), row("dgProfile", self.profile(), path=e.profile_dir(self.out, self.profile())),
                      row("dgSettings", self.out / e.SETTINGS_FILE, path=self.out)])
        # game
        exe = e.ci_join(gd, "SkyrimSE.exe") if gd else None
        gv = e.pe_file_version(exe) if exe else None
        gvs = ".".join(map(str, gv)) if gv else None
        libs = e.steam_libraries()
        rows = [row("dgGameDir", f'{gd}  ({"dgSaved" if st.get("game_dir") else "dgAuto"})' if gd else None, bool(gd), gd),
                row("dgExe", gvs, bool(gvs), exe),
                (lambda sr: row("dgSrsVer", f'Steam {sr.get("sourceVersion")} → SKSE {sr.get("targetVersion")}'
                                + ("" if sr.get("_deployed") else " (dgSrsNotDeployed)"), True if sr.get("_deployed") else None)
                 if sr else None)(e.find_srs(gd, m_dir) if gd else None),
                row("dgData", e.ci_join(gd, "Data") if gd else None, bool(gd and e.ci_join(gd, "Data")), e.ci_join(gd, "Data") if gd else None)]
        if gd:
            appdata, docs = e.prefix_dirs(gd)
            pfx = gd.parent.parent / "compatdata" / e.SKYRIM_APPID / "pfx"
            ptxt = e.plugins_txt_path(gd)
            rows += [row("dgPrefix", pfx, pfx.is_dir(), pfx if pfx.is_dir() else None),
                     row("dgPluginsTxt", ptxt, bool(ptxt and ptxt.is_file()), ptxt.parent if ptxt and ptxt.parent.is_dir() else None, warn=True),
                     row("dgSaves", ci if (ci := e.ci_join(docs, "Saves")) else docs / "Saves", bool(ci), ci or (docs if docs.is_dir() else None), warn=True)]
        rows += [row("dgLibs", "; ".join(map(str, libs)) or None, bool(libs) if not gd else None),
                 row("dgRunning", "dgYes" if e.game_running() else "dgNo")]
        sec("dgGame", rows)
        # SKSE
        rep = self.read_json("skse_report.json") or {}
        loader = e.ci_join(gd, "skse64_loader.exe") if gd else None
        want = "skse64_{}_{}_{}.dll".format(*gv[:3]) if gv else None
        dll = e.ci_join(gd, want) if gd and want else None
        al = rep.get("address_library") or {}
        log = e.find_skse_log(gd, None) if gd else None
        srs = e.find_srs(gd, m_dir) if gd else None
        sec("dgSkse", [row("dgLoader", loader, bool(loader), loader, warn=True),
                       row("dgRuntimeDll", f"{want} — {'dgFound' if dll else 'dgMissing'}" if want else None, bool(dll) if want else None, warn=True),
                       row("dgAddrLib", (", ".join(al.get("found_in") or []) or "dgMissing") + (f' ({al.get("expected")})' if al.get("expected") else "") if al else "dgNotChecked",
                           bool(al.get("found_in")) if al else None, warn=True),
                       row("dgSkseLog", f"{log}  ({mtime(log)})" if log else (st.get("skse_log") or "dgNotFound"), bool(log) if gd else None,
                           log.parent if log else None, warn=True),
                       row("dgSkseCheck", mtime(self.out / "skse_report.json") or "dgNotChecked",
                           (not rep.get("warnings")) if rep else None, warn=True),
                       row("dgSrs", "dgNo") if not srs else None])  # when present it has its own line under the game
        # launcher
        j = self.read_json(e.LAUNCHER_JOURNAL)
        launcher = e.ci_join(gd, "SkyrimSELauncher.exe") if gd else None
        is_skse = bool(launcher and loader and launcher.stat().st_size == loader.stat().st_size
                       and e.file_digest(launcher) == e.file_digest(loader))
        sec("dgLauncher", [row("dgLauncherState", "dgLauncherSkse" if is_skse else "dgLauncherOrig" if launcher else "dgMissing",
                               None if launcher else False, launcher, warn=True),
                           row("dgLauncherJournal", ("dgYes" if j else "dgNo") + (" — dgLauncherStale" if j and not is_skse else ""),
                               False if j and not is_skse else None, warn=True),
                           row("dgLauncherBackup", (self.out / j["backup"]) if j and j.get("backup") else None, None,
                               (self.out / j["backup"]).parent if j and j.get("backup") else None)])
        # LOOT
        lm = self.read_json("loot/masterlist.json") or {}
        age = (time.time() - lm["fetched"]) / 86400 if lm.get("fetched") else None
        sec("dgLoot", [row("dgLootOn", "dgYes" if st.get("loot") is not False else "dgNo"),
                       row("dgMasterlist", f'{self.out / "loot" / "masterlist.yaml"}  ({lm.get("branch", "?")}, {age:.0f} d)' if age is not None else "dgNotDownloaded",
                           (age < 30) if age is not None else None, self.out / "loot" if (self.out / "loot").is_dir() else None, warn=True)])
        # Nexus
        acc = e.nexus_account()
        apps = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share") / "applications" / e.NXM_DESKTOP
        mime = ""
        if shutil.which("xdg-mime"):
            try:
                mime = subprocess.run(["xdg-mime", "query", "default", "x-scheme-handler/nxm"], capture_output=True,
                                      text=True, timeout=5, check=False).stdout.strip()
            except (OSError, subprocess.SubprocessError):
                mime = ""
        sec("dgNexus", [row("dgAccount", f'{acc.get("name")} ({"Premium" if acc.get("is_premium") else "Free"})' if acc else "dgNotLogged", bool(acc), warn=True),
                        row("dgNexusCfg", e.nexus_config_path(), None, e.nexus_config_path().parent if e.nexus_config_path().parent.is_dir() else None),
                        row("dgNxm", (str(apps) if apps.exists() else ("dgNo — dgNxmOptional" if acc and acc.get("is_premium") else "dgNo — dgNxmNeeded"))
                            + (f"  (xdg-mime: {mime})" if mime else ""),
                            # with Premium the program downloads by itself: the handler is only an extra
                            (apps.exists() and (not mime or mime == e.NXM_DESKTOP)) if (apps.exists() or not (acc and acc.get("is_premium"))) else None,
                            apps.parent if apps.exists() else None, warn=True),
                        row("dgApi", e.NEXUS_API) if e.NEXUS_API != "https://api.nexusmods.com" else None])
        # folders
        def count(d, pred):
            try:
                return sum(1 for x in d.iterdir() if pred(x)) if d.is_dir() else 0
            except OSError:
                return 0
        n_arch = count(a_dir, lambda x: x.is_file() and e.is_archive(x))
        same_fs = None
        if gd and m_dir.exists():
            try:
                same_fs = os.stat(m_dir).st_dev == os.stat(gd).st_dev
            except OSError:
                same_fs = None
        ow = e.profile_dir(self.out, self.profile()) / "overwrite"
        sec("dgFolders", [row("dgArchives", f"{a_dir}  ({n_arch})", a_dir.is_dir(), a_dir if a_dir.is_dir() else None),
                          row("dgMods", f"{m_dir}  ({count(m_dir, lambda x: x.is_dir())})", m_dir.is_dir(), m_dir if m_dir.is_dir() else None),
                          row("dgSameFs", "dgHardlinks" if same_fs else "dgCopies" if same_fs is False else None, same_fs, warn=True),
                          row("dgOutput", self.out, None, self.out),
                          row("dgOverwrite", f"{ow}  ({sum(1 for x in ow.rglob('*') if x.is_file()) if ow.is_dir() else 0})", None, ow if ow.is_dir() else None)])
        # deployment and backups
        jr = e.load_journal(self.out)
        b = e.load_baseline(self.out)
        sf, ss = e.store_stats(self.out)
        gb = e.list_backups(self.out)
        sec("dgBackups", [
            row("dgDeployed", f'{jr.get("profile")} · {jr.get("at")} · {len(jr.get("planned", jr.get("files", [])))}' if jr else "dgNo",
                bool(jr.get("complete", True)) if jr else None),
            row("dgBaseline", f'{b.get("game_version")} · {b.get("created")} · {len(b.get("files", {}))}' if b else "dgNo",
                (b.get("game_version") == gvs if gvs else True) if b else False, self.out if b else None, warn=True),
            row("dgIncremental", f'{"dgOn" if e.incremental_enabled(self.out) else "dgOff"} · {sf} · {e.human_size(ss)}',
                True if e.incremental_enabled(self.out) else None),
            row("dgFullBackups", f"{len(gb)} · {e.human_size(sum(x.get('size') or 0 for x in gb))}" if gb else "0",
                True if gb else None, self.out / "game_backups" if (self.out / "game_backups").is_dir() else None),
            row("dgDeployBackup", self.out / "backup", None, self.out / "backup" if (self.out / "backup").is_dir() else None)])
        # tools
        tools = e.find_tools()
        sec("dgTools", [row("dgArcTools", ", ".join(sorted(tools)) or None, bool(tools)),
                        row("dgRar", "unrar / 7z" if ("unrar" in tools or "7z" in tools) else None, "unrar" in tools or "7z" in tools, warn=True),
                        row("dgSteam", shutil.which("steam"), bool(shutil.which("steam")), warn=True),
                        row("dgXdgOpen", shutil.which("xdg-open"), bool(shutil.which("xdg-open")), warn=True)])
        # quick buttons on top: the folders players open most
        quick = []
        for key in ("dgGameDir", "dgData", "dgMods", "dgArchives", "dgSaves", "dgPluginsTxt", "dgPrefix", "dgSkseLog", "dgOverwrite", "dgOutput"):
            for s_ in out:
                for r in s_["rows"]:
                    if r["k"] == key and r["path"]:
                        quick.append({"k": key, "path": r["path"]})
        out.insert(0, {"key": "dgQuick", "rows": [], "quick": quick})
        self._diag_paths = {r["path"] for s_ in out for r in s_["rows"] if r["path"]}
        return out

    def crash_alert(self, gdir: Path | None) -> bool:
        """A crash log from the last game session that the logs page has not shown yet (checked every 10 s)."""
        now = time.time()
        c = getattr(self, "_crash_chk", None)
        if c and now - c[0] < 10:
            return c[1]
        alert = False
        if gdir:
            try:
                ov = self.e.game_log_overview(gdir, with_counts=False)
                lc = ov["last_crash"]
                alert = bool(lc and lc.get("fresh") and lc["mtime"] > float(self.e.read_settings(self.out).get("crash_seen") or 0))
            except OSError:
                alert = False
        self._crash_chk = (now, alert)
        return alert

    def game_logs(self) -> dict:
        e = self.e
        e.load_saved_paths(self.out)
        gd = e.find_game_dir(None)
        if not gd:
            return {"files": [], "no_game": True}
        if not hasattr(self, "glog_cache"):
            self.glog_cache = {}
        ov = e.game_log_overview(gd, True, self.glog_cache)
        if ov["last_crash"] and ov["last_crash"]["mtime"] > float(e.read_settings(self.out).get("crash_seen") or 0):
            e.update_settings(self.out, crash_seen=ov["last_crash"]["mtime"])
            self._crash_chk = None
        self._log_paths = {f["path"] for f in ov["files"]}
        paths = self._log_paths | {str(Path(f["path"]).parent) for f in ov["files"]}
        _, docs = e.prefix_dirs(gd)
        for sub in ("SKSE", "Logs/Script"):
            d = e.ci_join(docs, *sub.split("/")) if docs.exists() else None
            if d:
                paths.add(str(d))
                ov.setdefault("dirs", []).append({"k": sub, "path": str(d)})
        self._log_dirs = paths
        return ov

    def game_log_read(self, path: str) -> dict:
        if path not in getattr(self, "_log_paths", set()):
            self.game_logs()
            if path not in self._log_paths:
                return {"error": "bad"}
        p = Path(path)
        try:
            text, cut = self.e.read_log_tail(p, 2 << 20)
            st = p.stat()
        except OSError as ex:
            return {"error": str(ex)}
        return {"text": text, "cut": cut, "size": st.st_size, "mtime": st.st_mtime, "path": path}

    def open_file(self, path: str) -> str:
        if path not in getattr(self, "_log_paths", set()):
            return "bad"
        opener = shutil.which("xdg-open")
        if not opener:
            return "no-opener"
        subprocess.Popen([opener, path], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return "ok"

    def open_any(self, path: str) -> str:
        """Open a folder shown on the diagnostics page (only those, nothing else)."""
        if path not in getattr(self, "_diag_paths", set()) and path not in getattr(self, "_log_dirs", set()):
            return "bad"
        p = Path(path)
        target = p if p.is_dir() else p.parent
        opener = shutil.which("xdg-open")
        if not opener or not target.exists():
            return "no-opener" if not opener else "bad"
        subprocess.Popen([opener, str(target)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        return "ok"

    # ------------------------------------------------------------------ adding archives (drag & drop, file picker)
    def drop_extract(self) -> bool:
        """Drop mode: True = unpack the dropped archive where it lies, no copy in the archives folder."""
        return self.e.read_settings(self.out).get("drop_mode") == "extract"

    def archive_target(self, name: str, size: int | None = None, a_dir: Path | None = None) -> tuple[Path | None, str]:
        """Where a new archive goes in the archives folder: (path, "") / (None, reason)."""
        name = Path(name.replace("\\", "/")).name.strip()
        if not name or name.startswith(".") or not self.e.is_archive(Path(name)):
            return None, "not-archive"
        if a_dir is None:
            a_dir, _ = self.e.configured_dirs(self.root, self.out)
        a_dir.mkdir(parents=True, exist_ok=True)
        dest = a_dir / self.e.safe_archive_name(name)
        if dest.exists():
            if size is not None and dest.stat().st_size == size:
                return None, "exists"
            stem, ext = dest.name, ""
            m = re.search(r"(\.tar)?\.[A-Za-z0-9]+$", stem)
            if m:
                stem, ext = stem[:m.start()], stem[m.start():]
            n = 2
            while (a_dir / f"{stem} ({n}){ext}").exists():
                n += 1
            dest = a_dir / f"{stem} ({n}){ext}"
        return dest, ""

    def import_paths(self, paths: list[str]) -> dict:
        """Archives dropped on the window are copied (never hard-linked: the archives folder gets its own
        file, so whatever happens to the original — edited, moved, cleaned up — the copy is unaffected)."""
        added, skipped = [], []
        for raw in paths:
            src = Path(raw)
            if not src.is_file():
                skipped.append([src.name, "not-archive"])
                continue
            a_dir, _ = self.e.configured_dirs(self.root, self.out)
            try:
                if src.resolve().parent == a_dir.resolve():
                    skipped.append([src.name, "exists"])
                    continue
            except OSError:
                pass
            dest, why = self.archive_target(src.name, src.stat().st_size)
            if not dest:
                skipped.append([src.name, why])
                continue
            part = dest.with_name(dest.name + ".part")
            try:
                shutil.copyfile(src, part)
                part.replace(dest)
                added.append(dest.name)
            except OSError as ex:
                part.unlink(missing_ok=True)
                skipped.append([src.name, str(ex)])
        return {"added": added, "skipped": skipped}

    def extract_paths(self, paths: list[str]) -> dict:
        """Drop mode "unpack right away": the archives are unpacked from where they are, nothing is copied."""
        ok = [str(Path(x)) for x in paths if Path(x).is_file() and self.e.is_archive(Path(x))]
        skipped = [[Path(x).name, "not-archive"] for x in paths if str(Path(x)) not in ok]
        if not ok:
            return {"skipped": skipped, "started": False}
        GUI_ACTIONS["drop_extract"] = []
        err = self.start("drop_extract", [a for x in ok for a in ("--extract", x)] + ["--yes"])
        return {"skipped": skipped, "started": not err, "error": err}

    def receive_upload(self, name: str, size: int, stream, temp: bool = False) -> dict:
        """Archive sent by the web page (drag & drop or file picker), written as .part, then renamed.
        temp: the browser gives no file path, so for "unpack right away" the file waits in
        mod_engine_output/incoming and is deleted after unpacking."""
        inc = self.out / "incoming"
        if temp:
            inc.mkdir(parents=True, exist_ok=True)
        dest, why = self.archive_target(name, size, inc if temp else None)
        if not dest:
            while size > 0:  # drain the body so the browser gets the answer
                chunk = stream.read(min(size, 1 << 20))
                if not chunk:
                    break
                size -= len(chunk)
            return {"skipped": why, "name": Path(name).name}
        part = dest.with_name(dest.name + ".part")
        got = 0
        try:
            with part.open("wb") as f:
                while got < size:
                    chunk = stream.read(min(size - got, 1 << 20))
                    if not chunk:
                        break
                    f.write(chunk)
                    got += len(chunk)
                    self.uploads[dest.name] = got
            if got != size:
                part.unlink(missing_ok=True)
                return {"error": "incomplete", "name": dest.name}
            part.replace(dest)
            return {"added": str(dest) if temp else dest.name}
        finally:
            self.uploads.pop(dest.name, None)

    def import_done(self, added: list[str]) -> str | None:
        """After adding archives: unpack them now, or tell that a job is busy."""
        if not added:
            return None
        inc = (self.out / "incoming").resolve()
        temp = [x for x in added if Path(x).is_absolute() and Path(x).resolve().parent == inc]
        if temp:
            r = self.extract_paths(temp)
            if r.get("error"):
                for x in temp:
                    Path(x).unlink(missing_ok=True)
            return r.get("error")
        self._append(self.e.tr("Додано в архіви: {0}", ", ".join(added)))
        return self.start("scan")

    def collection_from_upload(self, data: bytes) -> str | None:
        try:
            c = json.loads(data.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError):
            return "not a collection"
        if not isinstance(c, dict) or not isinstance(c.get("mods"), list):
            return "not a collection"
        up = self.out / "uploads"
        up.mkdir(parents=True, exist_ok=True)
        f = up / "collection.json"
        f.write_bytes(data)
        GUI_ACTIONS["collection_set"] = []
        return self.start("collection_set", ["--collection", str(f), "--scan-only"])

    # ------------------------------------------------------------------ FOMOD wizard
    def fomod_start(self, mod: str | None, redo: bool = True) -> str | None:
        """Install one FOMOD (or every new one) with the choices made in the GUI wizard."""
        args = ["--fomod-redo" if redo else "--fomod", "--scan-only"] + (["--fomod-mod", mod] if mod else [])
        GUI_ACTIONS["fomod_choose"] = []
        return self.start("fomod_choose", args, interactive=True)

    def answer(self, qid: int, picked: list | None = None, cancel: bool = False) -> str | None:
        with self.lock:
            q, proc = self.question, self.proc
            if not q or q.get("id") != qid or not proc or not proc.stdin:
                return "no question"
            self.question = None
        try:
            proc.stdin.write((json.dumps({"cancel": True} if cancel else {"picked": [int(i) for i in picked or []]}) + "\n").encode("utf-8"))
            proc.stdin.flush()
        except (OSError, ValueError):
            return "job ended"
        return None

    def fomod_image(self, path: str) -> Path | None:
        """Only images inside the mods folder (FOMOD option pictures) are served to the page."""
        _, m_dir = self.e.configured_dirs(self.root, self.out)
        try:
            p = Path(path).resolve()
            p.relative_to(m_dir.resolve())
        except (ValueError, OSError):
            return None
        return p if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp") else None

    # ------------------------------------------------------------------ Nexus mod browser
    def installed_nexus_ids(self) -> set[int]:
        ids = set()
        for rel in (self.read_json("processed_archives.json") or {}):
            nid = gui_archive_meta(Path(rel).name).get("nexus_id")
            if nid:
                ids.add(nid)
        return ids

    def browse(self, query: str = "", sort: str = "endorsements", offset: int = 0, count: int = 20, adult: bool = False) -> dict:
        e = self.e
        acc = e.nexus_account()
        key = acc.get("api_key") if acc else None
        try:
            r = e.nexus_search(query, sort, offset, count, key, adult)
        except e.NexusError as ex:
            if not (key and not query.strip() and offset == 0):
                return {"error": str(ex)}
            try:  # without search the v1 lists still work for a logged-in user
                r = e.nexus_list_v1({"updated": "updated", "new": "new"}.get(sort, "trending"), key)
            except e.NexusError as ex2:
                return {"error": str(ex2)}
        hidden = sum(1 for m in r["mods"] if m.get("adult")) if not adult else 0
        if hidden:
            r["mods"] = [m for m in r["mods"] if not m.get("adult")]
        have = self.installed_nexus_ids()
        for m in r["mods"]:
            m["installed"] = m.get("mod_id") in have
            m["url"] = f"https://www.nexusmods.com/{e.NEXUS_GAME}/mods/{m.get('mod_id')}"
        r.setdefault("has_more", False)
        tot = r.get("total")
        r.update(hidden_adult=hidden, offset=offset, count=count, page=offset // count + 1,
                 pages=(max(1, -(-tot // count)) if isinstance(tot, int) and tot >= 0 else None),
                 premium=bool(acc and acc.get("is_premium")), logged_in=bool(acc))
        return r

    def browse_collections(self, query: str = "", sort: str = "endorsements", offset: int = 0, count: int = 20, adult: bool = False) -> dict:
        e = self.e
        acc = e.nexus_account()
        try:
            r = e.nexus_collections_search(query, sort, offset, count, acc.get("api_key") if acc else None, adult)
        except e.NexusError as ex:
            return {"error": str(ex)}
        pd = e.profile_dir(self.out, self.profile())
        src = self.read_json(str((pd / "collection_source.json").relative_to(self.out))) or {} if (pd / e.COLLECTION_FILE).exists() else {}
        cur_slug = src.get("slug")
        for c in r["collections"]:
            c["url"] = f"https://www.nexusmods.com/games/{e.NEXUS_GAME}/collections/{c.get('slug')}"
            c["attached"] = bool(cur_slug and cur_slug == c.get("slug"))
        tot = r.get("total")
        r.update(offset=offset, count=count, page=offset // count + 1,
                 pages=(max(1, -(-tot // count)) if isinstance(tot, int) and tot >= 0 else None),
                 premium=bool(acc and acc.get("is_premium")), logged_in=bool(acc))
        return r

    def mod_files(self, mod_id: int) -> dict:
        e = self.e
        acc = e.nexus_account()
        if not acc:
            return {"error": "login"}
        try:
            return {"files": e.nexus_mod_files(int(mod_id), acc["api_key"]), "premium": bool(acc.get("is_premium"))}
        except e.NexusError as ex:
            return {"error": str(ex)}

    def download(self, mod_id: int, file_id: int | None) -> dict:
        """Premium: the program downloads and unpacks. Free: the file page opens on the site, its
        "Mod Manager Download" button hands an nxm:// link to this program (needs the nxm handler)."""
        acc = self.e.nexus_account()
        if acc and acc.get("is_premium"):
            GUI_ACTIONS["browser_download"] = []
            err = self.start("browser_download", ["--nexus-download", f"{int(mod_id)}:{int(file_id)}" if file_id else str(int(mod_id)), "--yes"])
            return {"error": err} if err else {"result": "job"}
        url = self.e.nexus_mm_url(int(mod_id), int(file_id) if file_id else None)
        return {"result": "browser" if self.open_url(url) else "no-opener", "url": url}

    def open_url(self, url: str) -> bool:
        if not url.startswith("https://"):
            return False
        try:
            if shutil.which("xdg-open"):
                subprocess.Popen(["xdg-open", url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, start_new_session=True)
                return True
            return webbrowser.open(url)
        except Exception:
            return False

    def thumb(self, url: str) -> Path | None:
        """Nexus picture cached on disk (for the native window; the page loads them itself)."""
        import hashlib
        import urllib.request
        if not url or not url.startswith(("https://", "http://127.0.0.1")):
            return None
        p = self.thumbs / (hashlib.sha1(url.encode()).hexdigest() + Path(urlparse(url).path).suffix[:5])
        if p.exists() and p.stat().st_size:
            return p
        try:
            self.thumbs.mkdir(parents=True, exist_ok=True)
            req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read(4 * 1024 * 1024)
            tmp = p.with_suffix(p.suffix + ".part")
            tmp.write_bytes(data)
            tmp.replace(p)
            return p
        except Exception:
            return None

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
            if u.path == "/api/file":
                f = app.fomod_image(q.get("path", [""])[0])
                if not f:
                    return self._send(404, b"not found", "text/plain")
                ctype = {".png": "image/png", ".gif": "image/gif", ".bmp": "image/bmp", ".webp": "image/webp"}.get(f.suffix.lower(), "image/jpeg")
                return self._send(200, f.read_bytes(), ctype)
            if u.path == "/api/browse":
                return self._json(app.browse(q.get("q", [""])[0], q.get("sort", ["endorsements"])[0],
                                             int(q.get("offset", ["0"])[0] or 0), 20, q.get("adult", ["0"])[0] == "1"))
            if u.path == "/api/logfile":
                return self._json(app.log_read(int(q.get("from", ["0"])[0] or 0),
                                               upto=int(q["to"][0]) if q.get("to", [""])[0].isdigit() else None))
            if u.path == "/api/browse_collections":
                return self._json(app.browse_collections(q.get("q", [""])[0], q.get("sort", ["endorsements"])[0],
                                                         int(q.get("offset", ["0"])[0] or 0), 20, q.get("adult", ["0"])[0] == "1"))
            if u.path == "/api/gamelogs":
                return self._json(app.game_logs())
            if u.path == "/api/gamelog":
                return self._json(app.game_log_read(q.get("path", [""])[0]))
            if u.path == "/api/diag":
                return self._json({"sections": app.diagnostics("web")})
            if u.path == "/api/mod_files":
                return self._json(app.mod_files(int(q.get("mod", ["0"])[0] or 0)))
            self._send(404, b"not found", "text/plain")

        def _post(self):
            u = urlparse(self.path)
            if not self._authed(parse_qs(u.query)):
                return self._send(403, b"forbidden", "text/plain")
            if u.path in ("/api/upload", "/api/upload_collection"):  # raw file body, not JSON
                q = parse_qs(u.query)
                size = int(self.headers.get("Content-Length") or 0)
                if u.path == "/api/upload_collection":
                    if size > 64 * 1024 * 1024:
                        return self._json({"error": "too big"}, 413)
                    err = app.collection_from_upload(self.rfile.read(size))
                    return self._json({"error": err} if err else {"ok": True}, 409 if err else 200)
                return self._json(app.receive_upload(q.get("name", [""])[0], size, self.rfile, app.drop_extract()))
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
                    if action == "plugin_order" and val.strip().startswith(("{", "[")):
                        # hundreds of names: a file, not a command line (it is also shown in the log)
                        tmp = app.out / "plugin_order_new.json"
                        tmp.parent.mkdir(parents=True, exist_ok=True)
                        tmp.write_text(val, encoding="utf-8")
                        val = str(tmp)
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
                if "drop_extract" in body:
                    app.e.update_settings(app.out, drop_mode="extract" if body["drop_extract"] else "copy")
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
            if p == "/api/import_done":
                err = app.import_done([str(x) for x in body.get("added") or []])
                return self._json({"error": err} if err else {"ok": True})
            if p == "/api/term":
                err = app.term_run(str(body.get("cmd") or ""))
                return self._json({"error": err} if err else {"ok": True}, 409 if err else 200)
            if p == "/api/term_input":
                err = app.term_input(str(body.get("text") or ""))
                return self._json({"error": err} if err else {"ok": True}, 409 if err else 200)
            if p == "/api/log_save":
                path = app.log_save()
                return self._json({"path": path} if path else {"error": "save"})
            if p == "/api/log_open":
                return self._json({"result": app.log_open()})
            if p == "/api/open_terminal":
                return self._json({"result": app.open_terminal()})
            if p == "/api/term_stop":
                err = app.term_stop()
                return self._json({"error": err} if err else {"ok": True})
            if p == "/api/fomod_start":
                err = app.fomod_start(str(body.get("mod") or "") or None, bool(body.get("redo", True)))
                return self._json({"error": err} if err else {"ok": True}, 409 if err else 200)
            if p == "/api/fomod_answer":
                err = app.answer(int(body.get("id") or 0), body.get("picked") or [], bool(body.get("cancel")))
                return self._json({"error": err} if err else {"ok": True}, 409 if err else 200)
            if p == "/api/download":
                r = app.download(int(body.get("mod") or 0), int(body["file"]) if body.get("file") else None)
                return self._json(r, 409 if r.get("error") else 200)
            if p == "/api/open_file":
                return self._json({"result": app.open_file(str(body.get("path") or ""))})
            if p == "/api/open_path":
                return self._json({"result": app.open_any(str(body.get("path") or ""))})
            if p == "/api/open_url":
                return self._json({"ok": app.open_url(str(body.get("url") or ""))})
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


# ---- the web interface in a window of its own ---------------------------------------------------------
# One interface to maintain: the page. It is shown, in this order, in
#   1. a WebKit view inside a GTK window (when WebKit2GTK is installed),
#   2. a Chromium-type browser in app mode (--app: a window without tabs or address bar) — native or Flatpak,
#   3. Firefox with its own profile whose toolbars are hidden (Firefox has no app mode),
#   4. a normal browser tab.
CHROMIUM_BINS = ("vivaldi", "vivaldi-stable", "chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
                 "brave", "brave-browser", "microsoft-edge", "microsoft-edge-stable")
CHROMIUM_FLATPAKS = ("com.vivaldi.Vivaldi", "org.chromium.Chromium", "com.google.Chrome", "com.brave.Browser",
                     "io.github.ungoogled_software.ungoogled_chromium", "com.microsoft.Edge")
FIREFOX_FLATPAK = "org.mozilla.firefox"
FIREFOX_USERJS = """user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("startup.homepage_welcome_url", "");
user_pref("startup.homepage_welcome_url.additional", "");
user_pref("browser.tabs.warnOnClose", false);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("datareporting.policy.dataSubmissionPolicyBypassNotification", true);
user_pref("dom.allow_scripts_to_close_windows", true);
user_pref("browser.startup.page", 0);
"""
FIREFOX_USERCHROME = """/* Skyrim Mod Engine: an app window, not a browser */
#TabsToolbar, #nav-bar, #PersonalToolbar, #titlebar, #sidebar-box, #sidebar-main { visibility: collapse !important; }
"""

def flatpak_apps() -> set[str]:
    if not shutil.which("flatpak"):
        return set()
    try:
        r = subprocess.run(["flatpak", "list", "--app", "--columns=application"], capture_output=True, text=True, timeout=10, check=False)
        return {x.strip() for x in r.stdout.splitlines() if x.strip()}
    except (OSError, subprocess.SubprocessError):
        return set()

def webkit_available() -> str | None:
    if os.environ.get("SME_NO_WEBKIT"):
        return None
    try:
        import gi
        for v in ("4.1", "4.0"):
            try:
                gi.require_version("WebKit2", v)
                gi.require_version("Gtk", "3.0")
                return v
            except ValueError:
                continue
    except ImportError:
        pass
    return None

def app_window_available() -> bool:
    """Is there anything to show the page as an app window (WebKit, a Chromium-type browser, Firefox)?"""
    if webkit_available() or any(shutil.which(b) for b in CHROMIUM_BINS + ("firefox",)):
        return True
    fl = flatpak_apps()
    return any(f in fl for f in CHROMIUM_FLATPAKS + (FIREFOX_FLATPAK,))

def gui_app_window(url: str, out: Path, prefer: str | None = None) -> tuple[str, subprocess.Popen | None]:
    """Open the page as an app window: (kind, process or None). Kinds: chromium:<name>, firefox, tab, none."""
    def spawn(cmd):
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    fl = flatpak_apps()
    order = ["chromium", "firefox"] if prefer != "firefox" else ["firefox", "chromium"]
    for kind in order:
        if kind == "chromium":
            # maximized, not a fixed 1280x800: on the Deck the taskbar and the title bar take part of the screen
            app_args = [f"--app={url}", "--start-maximized", "--no-first-run", "--no-default-browser-check"]
            for b in CHROMIUM_BINS:
                if shutil.which(b):
                    try:
                        return f"chromium:{b}", spawn([shutil.which(b)] + app_args)
                    except OSError:
                        pass
            for fp in CHROMIUM_FLATPAKS:
                if fp in fl:
                    try:
                        return f"chromium:{fp}", spawn(["flatpak", "run", fp] + app_args)
                    except OSError:
                        pass
        else:
            native = shutil.which("firefox")
            flat = FIREFOX_FLATPAK in fl
            if not (native or flat):
                continue
            # A profile only for SME (the player's own profile is not touched); a Flatpak Firefox can only
            # reach its own data folder, so the profile goes there.
            prof = (Path.home() / ".var/app" / FIREFOX_FLATPAK / "sme-profile") if (flat and not native) else (out / "cache" / "firefox-profile")
            try:
                (prof / "chrome").mkdir(parents=True, exist_ok=True)
                (prof / "user.js").write_text(FIREFOX_USERJS, encoding="utf-8")
                (prof / "chrome" / "userChrome.css").write_text(FIREFOX_USERCHROME, encoding="utf-8")
                cmd = ([native] if native else ["flatpak", "run", FIREFOX_FLATPAK]) + ["--new-instance", "--no-remote", "--profile", str(prof), url]
                return "firefox", spawn(cmd)
            except OSError:
                pass
    return ("tab", None) if gui_open_browser(url, out) else ("none", None)

def gui_webkit_window(app, url: str, version: str) -> None:
    """The page inside a GTK window (WebKit2GTK). Runs the GTK main loop; returns when the window closes."""
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("WebKit2", version)
    from gi.repository import Gtk, WebKit2
    win = Gtk.Window(title="Skyrim Mod Engine")
    win.set_default_size(1280, 800)
    ctx = WebKit2.WebContext.get_default()
    view = WebKit2.WebView.new_with_context(ctx)
    st = view.get_settings()
    st.set_enable_developer_extras(True)       # right click → Inspect, for bug hunting
    st.set_javascript_can_access_clipboard(True)

    def on_decide(_v, decision, dtype):
        # Links to other sites (Nexus pages) open in the normal browser, not inside the app window.
        try:
            if dtype in (WebKit2.PolicyDecisionType.NAVIGATION_ACTION, WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION):
                uri = decision.get_navigation_action().get_request().get_uri()
                if uri and not uri.startswith(url.split("/?")[0]):
                    decision.ignore()
                    app.open_url(uri)
                    return True
        except Exception:
            pass
        return False
    view.connect("decide-policy", on_decide)

    # Files dropped on the window are handled here, natively: real paths, no upload through the page
    # (and "unpack right away" then really unpacks from where the file lies).
    from gi.repository import Gdk, GLib

    def on_drop(_w, dctx, _x, _y, data, _info, tm):
        _w.stop_emission_by_name("drag-data-received")
        paths = []
        for u in data.get_uris() or []:
            try:
                paths.append(GLib.filename_from_uri(u)[0])
            except Exception:
                pass
        Gtk.drag_finish(dctx, bool(paths), False, tm)
        if not paths:
            return
        colls = [x for x in paths if x.lower().endswith(".json")]
        arcs = [x for x in paths if not x.lower().endswith(".json")]
        if colls:
            try:
                err = app.collection_from_upload(Path(colls[0]).read_bytes())
            except OSError as ex:
                err = str(ex)
            if err:
                app._append(f"collection.json: {err}")
        if not arcs:
            return

        def work():
            if app.drop_extract():
                r = app.extract_paths(arcs)
            else:
                r = app.import_paths(arcs)
                if r["added"]:
                    app.import_done(r["added"])
            for name, why in r.get("skipped") or []:
                app._append(app.e.tr("Пропущено: {0} ({1})", name, why))
        threading.Thread(target=work, daemon=True).start()
    view.drag_dest_set(Gtk.DestDefaults.ALL, [Gtk.TargetEntry.new("text/uri-list", 0, 0)], Gdk.DragAction.COPY)
    view.connect("drag-data-received", on_drop)
    view.connect("close", lambda *_: win.destroy())  # window.close() from the page (the ✕ button)
    win.add(view)

    def on_close(*_):
        if app.job and app.job.get("running"):
            d = Gtk.MessageDialog(transient_for=win, modal=True, message_type=Gtk.MessageType.WARNING,
                                  buttons=Gtk.ButtonsType.OK, text=app.e.tr("Йде робота — зачекай, поки вона закінчиться."))
            d.run()
            d.destroy()
            return True
        return False
    win.connect("delete-event", on_close)
    win.connect("destroy", lambda *_: Gtk.main_quit())
    view.load_uri(url)
    win.show_all()
    win.maximize()
    Gtk.main()

def gui_run(engine, engine_path: Path, output: str = "mod_engine_output", open_page: bool = True, mode: str = "tab") -> int:
    os.chdir(engine_path.parent)  # the engine keeps Mods/, mods-archives/ and its output next to itself
    app = GuiApp(engine, engine_path, output)
    app.out.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("SME_GUI_PORT", "0"))), gui_make_handler(app))
    url = f"http://127.0.0.1:{srv.server_address[1]}/?t={app.token}"
    print(engine.tr("Графічний інтерфейс: {0}", url), flush=True)
    wk = webkit_available() if (open_page and mode == "app" and not os.environ.get("SME_NO_WEBKIT")) else None
    proc, kind = None, ""
    if open_page and not wk:
        if mode == "app":
            kind, proc = gui_app_window(url, app.out, os.environ.get("SME_BROWSER"))
        else:
            kind = "tab" if gui_open_browser(url, app.out) else "none"
        print(engine.tr("Вікно інтерфейсу: {0}", kind), flush=True)
        if kind == "none":
            msg = engine.tr("Не вдалося відкрити браузер. Відкрий цю адресу вручну:\n{0}", url)
            if shutil.which("kdialog"):
                subprocess.Popen(["kdialog", "--msgbox", msg], start_new_session=True)
            print(msg, flush=True)

    if not engine.load_baseline(app.out) and engine.find_game_dir(None):
        engine.GUI_ACTIONS["baseline_auto"] = ["--game-baseline"]
        app.start("baseline_auto")  # the automatic file list, shown live in the event log

    def watchdog():
        # Stop when the page is gone: no heartbeat for 60 s (or none at all for 10 min), no job running.
        # A Firefox started with its own profile is our window: when that process ends, so do we (after a job).
        # (A Chromium-type browser hands the window to an already running instance and exits at once,
        # so only the Firefox with its own profile counts as "our window".)
        own = kind == "firefox" and proc is not None and proc.poll() is None
        while not app.stop.wait(2):
            idle = time.time() - (app.last_beat or app.started)
            limit = 60 if app.last_beat else 600
            gone = own and proc.poll() is not None and time.time() - app.started > 5
            if (idle > limit or gone) and not (app.job and app.job.get("running")):
                app.stop.set()
        srv.shutdown()
    threading.Thread(target=watchdog, daemon=True).start()
    if wk:
        threading.Thread(target=lambda: srv.serve_forever(poll_interval=0.5), daemon=True).start()
        print(engine.tr("Вікно інтерфейсу: {0}", f"webkit {wk}"), flush=True)
        try:
            gui_webkit_window(app, url, wk)
        finally:
            while app.job and app.job.get("running"):  # never leave a deployment half-done
                time.sleep(0.5)
            app.stop.set()
        srv.server_close()
        return 0
    try:
        srv.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    srv.server_close()
    return 0


GUI_PAGE = r"""<!doctype html>
<html lang="uk" translate="no"><head><meta charset="utf-8"><meta name="google" content="notranslate"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Skyrim Mod Engine</title>
<style>
:root{color-scheme:dark;--bg:#0b1220;--panel:#111a2b;--panel2:#152035;--line:#22314c;--text:#e6ecf5;--mut:#8a9ab5;--acc:#2f7fd4;--acc2:#3b93ee;
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
.main{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:14px;padding:14px;min-height:0;overflow:auto}
.col{display:flex;flex-direction:column;gap:12px;min-height:0;min-width:0}
/* long names and labels wrap instead of widening the page (the column is minmax(0,1fr)) */
.btn{white-space:normal;max-width:100%;text-align:left}.page td,.page li,.page b{overflow-wrap:anywhere}
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
.log .rt{display:flex;align-items:center;gap:10px;padding:0 14px;color:var(--mut)}#jobtxt{min-width:0;max-width:46vw;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.dot{width:9px;height:9px;border-radius:50%;background:var(--ok);display:inline-block}.dot.run{background:var(--warn);animation:p 1s infinite}
@keyframes p{50%{opacity:.3}}
.page{padding:16px;overflow:auto;flex:1;min-height:0}.page h2{margin:0 0 6px;font-size:18px}.page p.m{color:var(--mut);margin:0 0 14px}
.grid2{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.box{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px}
.mono{font-family:ui-monospace,"DejaVu Sans Mono",monospace;font-size:12.5px}
.empty{color:var(--mut);padding:30px;text-align:center}
.search{min-width:0}.search input{min-width:0;width:100%}th.c-num{width:44px}
.mname>div{min-width:0}.mname .n{overflow-wrap:anywhere}
/* modal dialogs: FOMOD wizard, mod files */
.ovl{position:fixed;inset:0;background:#000a;display:grid;place-items:center;z-index:50;padding:16px}
.dlg{background:var(--panel);border:1px solid var(--line);border-radius:12px;width:min(980px,100%);max-height:calc(100vh - 32px);display:flex;flex-direction:column;box-shadow:0 20px 60px #000c}
.dlg .dh{padding:14px 16px;border-bottom:1px solid var(--line)}.dlg .dh b{font-size:16px}.dlg .dh .m{color:var(--mut);font-size:12.5px;margin-top:3px}
.dlg .db{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:0;min-height:0;flex:1;overflow:hidden}
.dlg .opts{overflow:auto;padding:10px;border-right:1px solid var(--line)}
.dlg .opt{display:flex;gap:10px;align-items:flex-start;padding:8px 10px;border-radius:8px;cursor:pointer}
.dlg .opt:hover,.dlg .opt.cur{background:var(--panel2)}.dlg .opt.dis{opacity:.5;cursor:not-allowed}
.dlg .opt input{margin-top:3px}.dlg .opt .k{font-size:11.5px;color:var(--mut)}
.dlg .desc{overflow:auto;padding:12px 14px;font-size:13px;white-space:pre-wrap}.dlg .desc img{max-width:100%;max-height:320px;border-radius:8px;display:block;margin-bottom:10px}
.dlg .df{padding:12px 16px;border-top:1px solid var(--line);display:flex;gap:10px;align-items:center;justify-content:flex-end;flex-wrap:wrap}
.dlg .err{color:var(--bad);margin-right:auto}
.dlg.files .db{display:block;overflow:auto;padding:10px 16px}
.ftab{width:100%;border-collapse:collapse;font-size:13px}.ftab td{padding:7px 6px;border-bottom:1px solid var(--line);vertical-align:top}
/* drag & drop */
body.dropping::after{content:attr(data-drop);position:fixed;inset:12px;border:3px dashed var(--acc2);border-radius:16px;background:#0b1220cc;
 display:grid;place-items:center;font-size:22px;color:#fff;z-index:60;pointer-events:none}
.toast{position:fixed;right:16px;bottom:136px;background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:10px 14px;z-index:55;box-shadow:0 8px 30px #0008}
/* mod browser */
.bgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:12px}
.bcard{background:var(--panel2);border:1px solid var(--line);border-radius:10px;overflow:hidden;display:flex;flex-direction:column}
.bcard .pic{height:130px;background:#0c1424 center/cover no-repeat;display:block;width:100%;object-fit:cover}
.bcard .bb{padding:10px 12px;display:flex;flex-direction:column;gap:6px;flex:1}
.bcard .bn{font-weight:600;overflow-wrap:anywhere}.bcard .bm{color:var(--mut);font-size:12px}
.bcard .bs{font-size:12.5px;color:#c4d0e3;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.bcard .ba{display:flex;gap:8px;margin-top:auto;flex-wrap:wrap}.bcard .ba .btn{padding:6px 10px;font-size:12.5px}
.badge{display:inline-block;background:#163d2a;color:#7ee2ad;border-radius:6px;padding:1px 7px;font-size:11.5px}
.badge.bad{background:#4a1f25;color:#ff9a94}.badge.old{background:#2a3346;color:#9fb0cc}.badge.up{background:#123a5c;color:#8fd0ff;white-space:nowrap}.glcrash{border-color:#6b2a30;margin:8px 0}.chip{display:inline-block;background:#1b2740;border:1px solid var(--line);border-radius:12px;padding:2px 9px;font-size:12.5px;margin:2px 4px 2px 0}
.gllist{max-height:34vh;overflow-y:auto;border:1px solid var(--line);border-radius:8px}.glrow{display:flex;flex-wrap:wrap;align-items:center;gap:6px 8px;padding:6px 10px;border-bottom:1px solid #1d2a40;cursor:pointer}.glrow:hover{background:#121c2d}.glrow.on{background:#16233a;box-shadow:inset 3px 0 0 var(--acc)}
.glname{min-width:0;overflow-wrap:anywhere}.glmeta{margin-left:auto;font-size:12px}.glrow .cnt{border-radius:10px;padding:1px 8px;font-size:12px}.glrow .cnt.bad{background:#4a1f25;color:#ff9a94}.cnt.wn{background:#3a2e12;color:#f3c672}.glout{min-height:240px;max-height:60vh;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere}.glout mark{background:#6b5a1a;color:#fff}
.chips button.on{border-color:var(--acc);color:#fff}.polist{list-style:none;margin:0;padding:0}.pogrp{color:#8fa3c4;font-size:12.5px;text-transform:uppercase;letter-spacing:.04em;margin:10px 0 4px}
.porow{display:flex;align-items:center;gap:8px;padding:5px 6px;border-bottom:1px solid #1d2a40;min-width:0}.porow:hover{background:#121c2d}.pooff .mono{opacity:.5;text-decoration:line-through}
.pon{width:2.6em;text-align:right;color:#7d8ba3;font-size:12.5px;flex:none}.poname{flex:1;min-width:0;overflow-wrap:anywhere;cursor:pointer}.pom{display:block;font-size:12px;margin-left:24px}
.pobtns{display:flex;gap:4px;flex:none}.btn.sm{padding:4px 9px;min-width:34px}.pomsg{background:#3a2e12;color:#f3c672;border-radius:8px;padding:6px 10px}.m.warn{color:#f3c672}
@media (max-width:760px){.dlg .db{grid-template-columns:1fr;overflow:auto}.dlg .opts{border-right:0;border-bottom:1px solid var(--line);overflow:visible}.dlg .desc{overflow:visible}}
/* Narrower windows (browser side panels, desktop scaling): shrink step by step, never cut content */
@media (max-width:1240px){.app{grid-template-columns:64px 1fr}.nav{justify-content:center;padding:9px 0}.nav span,.nav .cnt,.side .about{display:none}
 .nav .cnt.bad{display:block;position:absolute;margin:-18px 0 0 22px;padding:0 5px;font-size:10px}.nav{position:relative}
 .main{grid-template-columns:minmax(0,1fr) 270px}.tag{display:none}.log{grid-template-columns:56px 1fr auto}.log .lt>div:last-child{display:none}}
@media (max-width:1040px){.c-ver{display:none}}
@media (max-width:960px){.main{grid-template-columns:minmax(0,1fr)}.rightcol{order:-1}.rightcol .detail{display:none}
 .status .kv{grid-template-columns:auto 1fr auto 1fr}.top-right span{display:none}}
@media (max-width:760px){.c-cat{display:none}.status .kv{grid-template-columns:auto 1fr}.mname .s{display:none}}
/* Steam Deck: 1280x800 minus the browser's own bars leaves ~650-700 px of height */
@media (max-height:860px){.hdr{height:58px}.brand{top:8px}.brand svg{width:38px;height:32px}.brand .t1{font-size:19px}.brand .t2{display:none}
 .tag{top:18px;font-size:13px}.top-right{top:8px;padding:3px 10px}.log{height:84px}.log .lt{padding:8px}.nav{padding:7px 10px}
 .main{padding:10px;gap:10px}.status{padding:10px}.launch{margin-top:8px}td{padding:5px 10px}.av{width:30px;height:30px;font-size:13px}.side .about{display:none}}
@media (max-height:560px){.hdr{display:none}.log{height:64px}}
/* the bottom bar is only a status line now; the full log and the terminal have their own page */
.log{height:auto!important;min-height:40px;grid-template-columns:auto 1fr auto!important}.log .lt{padding:0 12px!important;align-items:center;cursor:pointer}
.log .ico{width:26px;height:26px;font-size:12px}.log pre{white-space:pre-wrap;word-break:break-word;overflow:auto;max-height:4.2em;padding:6px 12px;line-height:1.35;cursor:pointer;align-self:center}
.log pre.bad,.termout .bad{color:#ff8f88}.termout .warn{color:#f3c672}.log .rt .pc{background:#4a1f25;color:#ff9a94;border-radius:10px;padding:1px 8px;font-size:12px;cursor:pointer}
.hrow{display:grid;grid-template-columns:20px minmax(110px,40%) 1fr auto;gap:8px;align-items:baseline;padding:6px 0;border-bottom:1px solid var(--line);font-size:13px}
.hrow:last-child{border-bottom:0}.hrow .hk{color:var(--mut)}.hrow .hv{overflow-wrap:anywhere}.hrow a.link{font-size:18px;line-height:1;padding:0 4px}
.termpage{display:flex;flex-direction:column;height:calc(100vh - 210px);min-height:320px}
.termout{flex:1;min-height:120px;margin:0;padding:10px 12px;overflow:auto;background:#070c16;border:1px solid var(--line);border-radius:8px;
 font:12.5px/1.45 ui-monospace,"DejaVu Sans Mono",monospace;color:#c4d0e3;white-space:pre-wrap;word-break:break-word}
.termout .part{color:#fff}.termin{display:flex;gap:8px;align-items:center;margin-top:10px}.termin .ps{font:13px ui-monospace,monospace;color:var(--ok)}
.termin input{flex:1;font:13px ui-monospace,"DejaVu Sans Mono",monospace;background:#070c16;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:9px 10px;outline:none}
.termin input:focus{border-color:var(--acc)}.chips{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}
.chips button{font:12px ui-monospace,monospace;padding:4px 8px;border-radius:6px;border:1px solid var(--line);background:var(--panel2);color:inherit;cursor:pointer}
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
const I18N={uk:{rmMod:"Видалити мод",rmFolders:"Буде видалено папки",rmArchive:"Видалити й архів",rmKeepNote:"Якщо зняти галочку, архів лишиться, але більше не розпаковуватиметься.",rmNoArchive:"Архів цього мода невідомий (мод покладено в папку вручну) — видаляються лише папки.",rmTrash:"Усе йде в кошик системи, якщо він є, — помилку можна виправити. Мод прибирається з усіх профілів.",rmInGame:"Мод зараз розгорнуто: його файли лишаться в грі до наступного розгортання.",autoAll:"Все автоматично",autoAllHint:"Розпакує нові архіви, за потреби встановить SKSE (з Premium-ключем), встановить FOMOD з автоматичним вибором і розгорне профіль. Гру не запускає.",autoDone:"Готово — можна грати",autoDoneNoSkse:"Готово, але без SKSE",autoDoneText:"Архіви розпаковано, FOMOD встановлено, моди розгорнуто в гру. Запускай Skyrim як зазвичай.",autoFail:"Не завершено",autoFailText:"Один із кроків не вдався — подробиці в журналі.",autoWarn:"Варто глянути",
srsFor:"для SKSE",srsNotDeployed:"мод ще не розгорнуто",srsSwapped:"зараз підмінений на старішу версію",hmIntro:"Коротко про твою збірку: що встановлено, чи все готово до гри і що варто глянути.",hmSetup:"Збірка",hmProfiles:"профілів",hmNoColl:"без колекції",hmGame:"Гра",hmSkseOk:"під цю версію гри",hmSkseBad:"не збігається з версією гри або немає",hmAttention:"Варто глянути",hmAllGood:"Проблем не знайдено",hmNxWhy:"Увійди, щоб шукати й завантажувати моди та колекції прямо з програми. Після входу ключ більше не показується.",hmGameDir:"Папка гри",hmKeyPage:"Відкрити сторінку ключів",
logTail:"[… показано останні 2 МБ журналу; увесь — «Відкрити файл журналу»]",logProblems:"Лише помилки й попередження",logOpen:"Відкрити файл журналу",logSave:"Зберегти копію",logSaved:"Журнал збережено:",term:"Журнал і термінал",termHint:"Тут увесь журнал подій і консоль SME: ті самі параметри, що й у терміналі (python3 mod_engine.py …). Це не системна оболонка — лише команди SME. Питання програми (вибір FOMOD, так/ні) відповідаються тут же.",termPh:"--help, --deploy --dry-run, --fomod-redo …",termAnswer:"відповідь програмі (Enter — за замовчуванням)",termRun:"Виконати",termSend:"Надіслати",termStop:"Зупинити",termClear:"Очистити",termKonsole:"Відкрити Konsole",termKonsoleHint:"Справжній термінал у папці SME; команда sme = python3 mod_engine.py",termNoTerm:"Термінал не знайдено (Konsole, GNOME Terminal, xterm…).",termReal:"Справжній термінал (bash у папці SME, команда sme). Журнал дій інтерфейсу — вище.",termOnlyOpts:"Лише параметри SME, що починаються з «-» (наприклад --help). Це не системна оболонка.",termNoGui:"Інтерфейс уже відкрито.",
diag:"Діагностика",glogs:"Логи гри й модів",glIntro:"Логи, які пишуть гра, SKSE, SKSE-плагіни модів, Crash Logger/Trainwreck і Papyrus — тут, без пошуку по префіксу Proton. Програма їх лише читає.",glNone:"Логів не знайдено — запусти гру хоча б раз через SKSE.",glNoGame:"Папку гри не знайдено — вкажи її в налаштуваннях.",glLoading:"Шукаю логи…",glCrash:"Останній краш-лог",glCrashFresh:"з останньої сесії",glMentions:"Найчастіше згадуються (підказка, не вирок — ім'я в краш-лозі ще не означає, що винен цей мод)",glOpen:"Показати",glOpenFile:"Відкрити файл",glOpenDir:"Відкрити папку",glRefresh:"Оновити",glCopy:"Копіювати",glCopied:"Скопійовано",glProb:"Лише помилки й попередження",glFind:"Знайти в лозі",glOld:"старий",glOldHint:"Старший за останній запуск гри — від попередньої сесії або від уже видаленого мода",glErr:"помилок",glWarn:"попереджень",glCut:"[… показано останні 2 МБ; увесь лог — «Відкрити файл»]",glPapOn:"Лог скриптів Papyrus увімкнено.",glPapOff:"Лог скриптів Papyrus вимкнено (так за замовчуванням) — без нього проблем зі скриптами не видно.",glPapNo:"Skyrim.ini не знайдено — запусти гру хоча б раз.",glPapEnable:"Увімкнути лог Papyrus",glPapDisable:"Вимкнути лог Papyrus",glPapHint:"Змінює [Papyrus] у Skyrim.ini префікса (оригінал зберігається). Діє з наступного запуску; трохи навантажує гру.",glSel:"Вибери лог зі списку.",glNoMatch:"Нічого не знайдено.",glGroups:"Усі",
dgIntro:"Шляхи, версії й стан усього, що використовує SME. «Копіювати звіт» — текст для повідомлення про проблему (без API-ключа).",dgCopy:"Копіювати звіт",dgCopied:"Звіт скопійовано",dgRefresh:"Оновити",dgLoading:"Збираю дані…",
dgSme:"Skyrim Mod Engine",dgVersion:"Версія",dgScript:"Скрипт",dgPython:"Python",dgGui:"Інтерфейс",dgProfile:"Профіль",dgSettings:"Налаштування",
dgGame:"Гра Skyrim SE/AE",dgGameDir:"Папка гри",dgSaved:"задано вручну",dgAuto:"знайдено автоматично",dgExe:"SkyrimSE.exe (версія)",dgSrsVer:"Зниження версії (Skyrim Runtime Swapper)",dgSrsNotDeployed:"мод ще не розгорнуто",dgData:"Папка Data",dgPrefix:"Префікс Proton",dgPluginsTxt:"Plugins.txt",dgSaves:"Збереження",dgLibs:"Бібліотеки Steam",dgRunning:"Гра запущена",
dgSkse:"SKSE",dgLoader:"skse64_loader.exe",dgRuntimeDll:"SKSE під цю версію гри",dgAddrLib:"Address Library",dgSkseLog:"skse64.log",dgSkseCheck:"Остання перевірка SKSE",dgSrs:"Skyrim Runtime Swapper",
dgLauncher:"SKSE Launcher (кнопка «Грати» в Steam)",dgLauncherState:"SkyrimSELauncher.exe",dgLauncherSkse:"підмінено на SKSE",dgLauncherOrig:"оригінальний",dgLauncherJournal:"Запис про підміну",dgLauncherStale:"лаунчер уже не наш (Steam відновив оригінал?)",dgLauncherBackup:"Збережений оригінал",
dgLoot:"LOOT",dgLootOn:"Сортування за LOOT",dgMasterlist:"Masterlist",dgNotDownloaded:"ще не завантажено (з'явиться при розгортанні)",
dgNexus:"Nexus",dgAccount:"Акаунт",dgNotLogged:"не виконано вхід",dgNexusCfg:"Файл входу",dgNxm:"Обробник nxm://",dgNxmOptional:"з Premium не обов'язковий: програма качає сама; потрібен лише для кнопки «Mod Manager Download» на сайті",dgNxmNeeded:"потрібен, щоб кнопка «Mod Manager Download» на сайті передавала файли сюди (Налаштування → Nexus)",dgApi:"API",
dgFolders:"Папки",dgArchives:"Архіви модів",dgMods:"Розпаковані моди",dgSameFs:"Моди й гра на одному диску",dgHardlinks:"так — жорсткі посилання, без зайвого місця",dgCopies:"ні — при розгортанні файли копіюються",dgOutput:"mod_engine_output",dgOverwrite:"overwrite профілю",
dgBackups:"Резервні копії й розгортання",dgDeployed:"Розгорнуто (профіль · час · файлів)",dgBaseline:"Список файлів гри (SHA-1)",dgIncremental:"Інкрементальний бекап (файлів · розмір)",dgFullBackups:"Повні бекапи (кількість · розмір)",dgDeployBackup:"Оригінали, замінені модами",
dgTools:"Інструменти",dgArcTools:"Розпаковувачі",dgRar:"Підтримка RAR",dgSteam:"Команда steam",dgXdgOpen:"xdg-open (відкриття папок і посилань)",
dgYes:"так",dgNo:"ні",dgOn:"увімкнено",dgOff:"вимкнено",dgFound:"є",dgMissing:"немає",dgNotFound:"не знайдено",dgNotChecked:"ще не перевірялось («Перевірити SKSE»)",
pickArchives:"Вибрати архіви",dropExtract:"Розпаковувати одразу, без копії в mods-archives",dropHintX:"Перетягнутий архів розпаковується одразу з того місця, де лежить; у mods-archives він не копіюється.",dropHereX:"Відпусти — архіви буде розпаковано одразу",extractSuffix:"Розпакувати нові (зайняті назви — в папку з суфіксом)",collisionBan:"Не розпаковано — папка з такою назвою вже є",
dropHint:"Архіви модів можна просто перетягнути у вікно — вони скопіюються в папку архівів і розпакуються.",dropHere:"Відпусти — архіви буде додано й розпаковано",
dropSkipped:"Пропущено",dropAddedBusy:"Архіви додано; розпакуй їх, коли закінчиться поточна робота.",skip_exists:"вже є",skip_not_archive:"не архів",skip_err:"помилка",skip_incomplete:"обірвано",
"skip_not-archive":"не архів",
launcherTitle:"SKSE через кнопку «Грати» в Steam",launcherOn:"Увімкнено: «Грати» в Steam запускає SKSE.",launcherOff:"Вимкнено: «Грати» в Steam запускає звичайний лаунчер.",
launcherHelp:"Підміняє SkyrimSELauncher.exe на skse64_loader.exe (оригінал зберігається). Не потрібно, якщо в збірці є Skyrim Runtime Swapper.",launcherSet:"Підмінити лаунчер на SKSE",launcherRestore:"Повернути оригінальний лаунчер",launcherRestoreQ:"Повернути оригінальний лаунчер? «Грати» в Steam запускатиме гру без SKSE.",
gameTitle:"Гра",gameDir:"Папка гри (там, де SkyrimSE.exe)",gameSaved:"Задано вручну",gameAuto:"Знайдено автоматично",gameMissing:"Папку гри не знайдено — вкажи її тут.",gameAutoBtn:"Шукати автоматично",
skseLog:"Свій шлях до skse64.log",skseLogDef:"стандартний — у префіксі Proton",pDefaultShort:"За замовчуванням",lootUse:"Сортувати плагіни за LOOT",lootHelp:"Вимкнено — лише власні правила (майстри, порядок модів, порядок колекції).",
nxmPaste:"Вставити посилання nxm:// (якщо браузер не передав його сам)",nxmGo:"Завантажити",nxmBad:"Потрібне посилання, що починається з nxm://",
collFile:"Підключити collection.json",collDetach:"Відключити колекцію",collNoneQ:"Відключити колекцію від цього профілю? Моди й порядок лишаться.",collDropQ:"Підключити «%s» як колекцію профілю «%p»?",collBadFile:"Це не файл колекції Vortex/Nexus.",
browse:"Браузер модів",fmChoose:"Вибрати варіанти",fmInstallManual:"Встановити нові з вибором",fmNone:"(нічого)",fmCancel:"Скасувати",fmNext:"Далі",fmClose:"Закрити",
brSearch:"Назва мода на Nexus Mods…",brGo:"Шукати",brSortEnd:"Найпопулярніші",brSortDl:"Найбільше завантажень",brSortUpd:"Нещодавно оновлені",brSortNew:"Нові",brLoading:"Завантаження…",
brHint:"Введи назву або просто натисни «Шукати».",brKindMods:"Моди",brKindColl:"Колекції",brCollSearch:"Пошук колекцій",brCollIntro:"Колекції — готові збірки модів з порядком і вибором FOMOD від автора. Найзручніше ставити колекцію в окремий профіль: твій поточний лишиться як є.",brCollMods:"модів",brCollNew:"У новий профіль",brCollCur:"У поточний профіль",brCollOn:"підключена",brCollLogin:"Щоб підключити колекцію, увійди в Nexus (Головна).",brCollPr:"Після підключення відкриється сторінка «Колекція»: звідти Premium качає всі архіви сам.",brCollFree:"Після підключення відкриється сторінка «Колекція» зі списком архівів: без Premium кожен качається кнопкою «Mod Manager Download» на сайті.",brCollProfName:"Назва нового профілю:",brCollCurQ:"Підключити колекцію «%s» до профілю «%p»? Порядок модів і вибір FOMOD братимуться з колекції.",brNothing:"Нічого не знайдено.",brFound:"Знайдено",brHidden:"приховано 18+",brHave:"вже є",brDownload:"Завантажити",brFiles:"Файли",brPage:"Сторінка",brPrev:"Назад",brPageOf:"сторінка %p з %n",brNext:"Далі",
brIntroPr:"Пошук на Nexus Mods. Premium: файл завантажиться й розпакується тут, без сайту.",brIntroFree:"Пошук на Nexus Mods. Без Premium Nexus дозволяє завантаження лише кнопкою на сайті: «Через сайт» відкриє потрібний файл, а кнопка «Mod Manager Download» передасть його сюди (потрібен обробник nxm у Налаштуваннях).",
brIntroAnon:"Пошук на Nexus Mods. Щоб завантажувати, увійди в Nexus у Налаштуваннях.",brNeedLogin:"Увійди в Nexus у Налаштуваннях (API-ключ).",brFilesOf:"Файли мода",brMain:"основний",brViaSite:"Через сайт",
brFreeNote:"Без Premium: відкриється сторінка файлу, натисни там «Mod Manager Download» (або «Slow download»). Програма отримає посилання nxm:// і завантажить сама.",brOpenManual:"Відкрий цю адресу в браузері:",brOpened:"відкрито в браузері",
tagline:"Твій світ. Твої моди. Твоя історія.",log:"Журнал подій",home:"Головна",mods:"Моди",fomod:"Пакети (FOMOD)",plugins:"Порядок плагінів",deps:"Залежності",conflicts:"Конфлікти",deploy:"Розгортання",backups:"Резервні копії",settings:"Налаштування",
about:"Більше, ніж просто мод-менеджер. Твій інструмент для створення світу.",ready:"Готово до гри",notdeployed:"Не розгорнуто",other:"У грі інший профіль",pending:"Є нерозгорнуті зміни",working:"Йде робота…",
profile:"Поточний профіль",enabledMods:"Модів увімкнено",totalSize:"Загальний розмір",lastDeploy:"Останнє розгортання",play:"Запустити гру",deployPlay:"Розгорнути й запустити",playOnly:"Лише запустити",dry:"Пробний прогін (без змін)",
addMods:"Додати моди",updCheck:"Перевірити оновлення",updCollHint:"Якщо це нова версія того самого мода: після перевірки оновлень «Розпакувати нові» замінить стару версію на місці.",updAll:"Оновити всі",updChecked:"Перевірено",updNever:"Оновлення ще не перевірялись",updNone:"нових версій немає",updAvail:"Є нові версії",updFilter:"Є оновлення",updNeedLogin:"Увійди в Nexus (Головна), щоб перевіряти оновлення модів.",updTitle:"Оновлення",updBtn:"Оновити",updSite:"Завантажити на сайті",updSiteHint:"Без Premium: натисни на сайті «Mod Manager Download», потім «Розпакувати нові» — нова версія замінить стару на місці.",updDownloaded:"Новий архів уже завантажено — розпакуй, щоб замінити стару версію.",updApply:"Розпакувати й замінити",updOutdated:"Файл на Nexus позначено застарілим, а заміни автор не вказав — подивись сторінку мода.",updGuessed:"Новий файл визначено за назвою (автор не позначив заміну) — перевір, що це той самий варіант.",updKeep:"Порядок, увімкнення і вибір FOMOD лишаються; старі папка й архів — у кошик. Потім — розгорнути.",
extract:"Розпакувати нові",installFomod:"Встановити пакети (FOMOD)",search:"Пошук модів…",allCats:"Усі категорії",onlyOff:"Лише вимкнені",onlyOn:"Лише увімкнені",
colName:"Назва моду",colCat:"Категорія",colVer:"Версія",colStatus:"Статус",active:"Активний",off:"Вимкнено",files:"файлів",pl:"плагін(и)",
up:"Вище",down:"Нижче",top:"На початок (низький пріоритет)",bottom:"В кінець (найвищий пріоритет)",openFolder:"Відкрити папку",
cat_skse:"SKSE",cat_ui:"Інтерфейс",cat_gfx:"Графіка",cat_audio:"Звук",cat_anim:"Анімації",cat_plugin:"Плагіни",cat_root:"Папка гри",cat_scripts:"Скрипти",cat_config:"Конфіги",cat_other:"Інше",
category:"Категорія",size:"Розмір",filesCount:"Файлів",version:"Версія",archive:"Архів",nexus:"Сторінка на NexusMods",dependsOn:"Залежить від",neededBy:"Від нього залежать",pluginsL:"Плагіни",sksePl:"SKSE-плагіни",bsa:"BSA-архіви",
fomodChoice:"Вибір FOMOD",wins:"Перекриває",loses:"Перекритий модами",depAnalysis:"Аналіз залежностей",allDepsOk:"Усі залежності задоволені",noSel:"Вибери мод у списку",order:"Позиція в порядку",
pendingBanner:"Порядок чи набір модів змінено — зміни потраплять у гру після розгортання.",deployNow:"Розгорнути",
attention:"Потребують уваги (не розгортаються)",fomodPage:"FOMOD-пакети профілю",fomodHint:"«Вибрати варіанти» — майстер установки, як у Vortex/MO2: кроки, описи й картинки варіантів. Нова установка йде в окрему папку, стара лишається, доки нова не готова; «Скасувати» нічого не змінює.",
installedTo:"Встановлено в",notInstalled:"Не встановлено",reinstallAuto:"Перевстановити всі (авто)",modeL:"Режим",
pluginsPage:"Порядок плагінів",poHelp:"Що нижче у списку — те вантажиться пізніше й перемагає, коли два плагіни змінюють один запис (NPC, предмет, левел-лист). Гра сама завжди ставить masters (ESM, ESL, ESP з прапорцем ESM) перед звичайними ESP, а кожен плагін — після своїх masters, тож кнопки не дадуть порушити ці правила.",poAuto:"Порядок: автоматичний (LOOT і правила рушія)",poCustom:"Порядок: власний — LOOT його не змінює",poDirty:"Є незбережені зміни",poNotApplied:"Власний порядок збережено, але ще не в plugins.txt — застосується при розгортанні цього профілю",poSave:"Зберегти й застосувати",poCancel:"Скасувати зміни",poReset:"Повернути автоматичний порядок",poResetQ:"Забути власний порядок і повернути LOOT/правила рушія? Застосується одразу, якщо розгортання актуальне.",poFilter:"Знайти плагін",poMasters:"Masters",poMasterGrp:"Masters (вантажаться першими)",poRegularGrp:"Звичайні плагіни",poBadMaster:"{0} не може стояти вище свого master {1}",poBadDep:"{0} — master для {1}, нижче нього не опуститься",poGroup:"{0} — master, гра не вантажить його серед звичайних ESP",poGroupR:"{0} — звичайний плагін, гра вантажить його тільки після всіх masters",poOffDeps:"Разом з {0} вимкнено плагіни, яким він потрібен: {1}",poOnMasters:"Разом з {0} увімкнено його masters: {1}",poMissing:"файла немає в Data",poOther:"Зараз розгорнуто інший профіль — розгорни цей, щоб керувати його порядком.",poPending:"Список модів змінився після розгортання — збережений порядок застосується при наступному розгортанні.",noDeploy:"Ще нічого не розгорнуто.",lootUpdate:"Оновити masterlist LOOT і перевірити",
depsPage:"Залежності між модами (за master-файлами плагінів)",problems:"Проблеми порядку",noProblems:"Проблем немає",
conflictsPage:"Конфлікти файлів",recompute:"Перерахувати",notComputed:"Конфлікти ще не рахувалися.",winner:"Перемагає",loser:"Перекритий",count:"Файлів",dupPlugins:"Один плагін у кількох модах",identical:"Однакових файлів (не конфлікт)",
deployPage:"Розгортання",deployBtn:"Розгорнути профіль",undeployBtn:"Повний відкат",skseBtn:"Перевірити SKSE",deployedProfile:"Розгорнутий профіль",filesDeployed:"Файлів у грі",incomplete:"Незавершене! Зроби відкат або розгорни знову.",
skseTitle:"SKSE",gameVer:"Версія гри",sksePlugins:"SKSE-плагіни (статично)",
backupsPage:"Резервні копії та файли профілю",backupFiles:"Оригінальних файлів гри в бекапі",changedSets:"Відкладені змінені файли",overwrite:"Overwrite профілю (створене/змінене грою)",openOw:"Відкрити overwrite",
settingsPage:"Налаштування",profiles:"Профілі",newProfile:"Новий профіль (порожній — моди вмикаєш сам)",npCollSrc:"collection.json або посилання на колекцію Nexus",npCollPh:"шлях або посилання",npFromColl:"Створити з колекції",
npHint:"Новий профіль бере лише порядок модів; усі моди вимкнені. З колекцією — увімкнені рівно моди колекції.",npNeed:"Вкажи назву профілю і файл або посилання колекції.",npFile:"З файлу collection.json",npLink:"З посилання Nexus",create:"Створити",del:"Видалити",switchTo:"Перемкнути",paths:"Папки",language:"Мова",quit:"Закрити інтерфейс",
busy:"Зачекай — триває інша операція.",confirmUndeploy:"Прибрати всі моди з гри й відновити оригінали?",confirmDel:"Видалити профіль «%s»?",profileName:"Назва профілю",idle:"Готово",
launchFail:"Не знайшов Steam для запуску. Запусти гру зі Steam.",gameRunning:"Гра вже запущена.",openArchives:"Відкрити папку архівів",noMods:"Модів ще немає. Поклади архіви в mods-archives і натисни «Розпакувати нові».",closed:"Інтерфейс закрито. Цю вкладку можна закрити.",
nexus:"Nexus Mods",nxLogged:"Увійшов як",nxPremium:"Premium",nxFree:"безкоштовний акаунт",nxNone:"Вхід не виконано.",nxKey:"Персональний API-ключ",nxKeyHelp:"nexusmods.com → аватар → Site preferences → API Keys → Personal API key. Ключ зберігається лише на цьому комп'ютері.",
nxLogin:"Увійти",nxLogout:"Вийти",nxHandler:"Приймати «Mod Manager Download» (nxm://)",nxHandlerHelp:"Без Premium Nexus дозволяє завантаження лише кнопкою «Mod Manager Download» на сайті: файл ляже в mods-archives.",
nxColl:"Завантажити відсутні архіви колекції",nxCollAll:"Завантажити разом з необов'язковими",nxDownload:"Завантажити з Nexus (ID або посилання)",nxAsk:"ID мода, ID:ID_файлу або посилання на сторінку мода",nxLoginFail:"Ключ не прийнято (див. журнал).",
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
en:{rmMod:"Remove mod",rmFolders:"These folders will be removed",rmArchive:"Remove the archive too",rmKeepNote:"Untick to keep the archive; it will not be unpacked again.",rmNoArchive:"This mod's archive is unknown (the mod was put into the folder by hand) — only the folders are removed.",rmTrash:"Everything goes to the system trash when there is one, so a mistake can be undone. The mod is removed from every profile.",rmInGame:"The mod is deployed now: its files stay in the game until the next deployment.",autoAll:"All automatic",autoAllHint:"Unpacks new archives, installs SKSE when needed (with a Premium key), installs FOMODs with automatic choices and deploys the profile. Does not start the game.",autoDone:"Done — ready to play",autoDoneNoSkse:"Done, but without SKSE",autoDoneText:"Archives unpacked, FOMODs installed, mods deployed into the game. Start Skyrim as usual.",autoFail:"Not finished",autoFailText:"One of the steps failed — details are in the log.",autoWarn:"Worth a look",
srsFor:"for SKSE",srsNotDeployed:"the mod is not deployed yet",srsSwapped:"currently swapped to the older version",hmIntro:"Your setup at a glance: what is installed, whether it is ready to play and what deserves a look.",hmSetup:"Setup",hmProfiles:"profiles",hmNoColl:"no collection",hmGame:"Game",hmSkseOk:"matches this game version",hmSkseBad:"does not match the game version or missing",hmAttention:"Worth a look",hmAllGood:"No problems found",hmNxWhy:"Log in to search and download mods and collections right from the program. After login the key is not shown again.",hmGameDir:"Game folder",hmKeyPage:"Open the API keys page",
logTail:"[… the newest 2 MB of the log are shown; all of it — “Open the log file”]",logProblems:"Errors and warnings only",logOpen:"Open the log file",logSave:"Save a copy",logSaved:"Log saved:",term:"Log and terminal",termHint:"The whole event log and the SME console: the same options as in a terminal (python3 mod_engine.py …). This is not a system shell — SME commands only. The program's questions (FOMOD choices, yes/no) are answered right here.",termPh:"--help, --deploy --dry-run, --fomod-redo …",termAnswer:"answer to the program (Enter — default)",termRun:"Run",termSend:"Send",termStop:"Stop",termClear:"Clear",termKonsole:"Open Konsole",termKonsoleHint:"A real terminal in the SME folder; the sme command = python3 mod_engine.py",termNoTerm:"No terminal found (Konsole, GNOME Terminal, xterm…).",termReal:"A real terminal (bash in the SME folder, the sme command). The interface's action log is above.",termOnlyOpts:"SME options only, starting with “-” (e.g. --help). This is not a system shell.",termNoGui:"The interface is already open.",
diag:"Diagnostics",glogs:"Game and mod logs",glIntro:"Logs written by the game, SKSE, mods' SKSE plugins, Crash Logger/Trainwreck and Papyrus — here, without digging through the Proton prefix. The program only reads them.",glNone:"No logs found — start the game through SKSE at least once.",glNoGame:"Game folder not found — set it in Settings.",glLoading:"Looking for logs…",glCrash:"Latest crash log",glCrashFresh:"from the last session",glMentions:"Mentioned most (a hint, not a verdict — a name in a crash log does not mean that mod is to blame)",glOpen:"Show",glOpenFile:"Open file",glOpenDir:"Open folder",glRefresh:"Refresh",glCopy:"Copy",glCopied:"Copied",glProb:"Errors and warnings only",glFind:"Find in log",glOld:"old",glOldHint:"Older than the last game start — from an earlier session or a mod already removed",glErr:"errors",glWarn:"warnings",glCut:"[… the newest 2 MB are shown; the whole log — “Open file”]",glPapOn:"The Papyrus script log is on.",glPapOff:"The Papyrus script log is off (the default) — without it script problems are invisible.",glPapNo:"Skyrim.ini not found — start the game at least once.",glPapEnable:"Turn on the Papyrus log",glPapDisable:"Turn off the Papyrus log",glPapHint:"Changes [Papyrus] in the prefix's Skyrim.ini (the original is kept). Takes effect on the next start; adds a little load.",glSel:"Pick a log from the list.",glNoMatch:"Nothing found.",glGroups:"All",
dgIntro:"Paths, versions and the state of everything SME uses. “Copy report” gives a text for a problem report (no API key).",dgCopy:"Copy report",dgCopied:"Report copied",dgRefresh:"Refresh",dgLoading:"Collecting…",
dgSme:"Skyrim Mod Engine",dgVersion:"Version",dgScript:"Script",dgPython:"Python",dgGui:"Interface",dgProfile:"Profile",dgSettings:"Settings",
dgGame:"Skyrim SE/AE",dgGameDir:"Game folder",dgSaved:"set manually",dgAuto:"found automatically",dgExe:"SkyrimSE.exe (version)",dgSrsVer:"Downgrade (Skyrim Runtime Swapper)",dgSrsNotDeployed:"the mod is not deployed yet",dgData:"Data folder",dgPrefix:"Proton prefix",dgPluginsTxt:"Plugins.txt",dgSaves:"Saves",dgLibs:"Steam libraries",dgRunning:"Game running",
dgSkse:"SKSE",dgLoader:"skse64_loader.exe",dgRuntimeDll:"SKSE for this game version",dgAddrLib:"Address Library",dgSkseLog:"skse64.log",dgSkseCheck:"Last SKSE check",dgSrs:"Skyrim Runtime Swapper",
dgLauncher:"SKSE Launcher (Steam's Play button)",dgLauncherState:"SkyrimSELauncher.exe",dgLauncherSkse:"replaced with SKSE",dgLauncherOrig:"original",dgLauncherJournal:"Replacement record",dgLauncherStale:"the launcher is no longer ours (Steam restored the original?)",dgLauncherBackup:"Saved original",
dgLoot:"LOOT",dgLootOn:"Sorting with LOOT",dgMasterlist:"Masterlist",dgNotDownloaded:"not downloaded yet (comes with deployment)",
dgNexus:"Nexus",dgAccount:"Account",dgNotLogged:"not logged in",dgNexusCfg:"Login file",dgNxm:"nxm:// handler",dgNxmOptional:"optional with Premium: the program downloads by itself; needed only for the site's “Mod Manager Download” button",dgNxmNeeded:"needed so that the site's “Mod Manager Download” button hands files to this program (Settings → Nexus)",dgApi:"API",
dgFolders:"Folders",dgArchives:"Mod archives",dgMods:"Extracted mods",dgSameFs:"Mods and game on the same drive",dgHardlinks:"yes — hard links, no extra space",dgCopies:"no — files are copied on deployment",dgOutput:"mod_engine_output",dgOverwrite:"Profile overwrite",
dgBackups:"Backups and deployment",dgDeployed:"Deployed (profile · time · files)",dgBaseline:"Game file list (SHA-1)",dgIncremental:"Incremental backup (files · size)",dgFullBackups:"Full backups (count · size)",dgDeployBackup:"Originals replaced by mods",
dgTools:"Tools",dgArcTools:"Unpackers",dgRar:"RAR support",dgSteam:"steam command",dgXdgOpen:"xdg-open (opening folders and links)",
dgYes:"yes",dgNo:"no",dgOn:"on",dgOff:"off",dgFound:"found",dgMissing:"missing",dgNotFound:"not found",dgNotChecked:"not checked yet (“Check SKSE”)",
pickArchives:"Choose archives",dropExtract:"Unpack right away, no copy in mods-archives",dropHintX:"A dropped archive is unpacked right from where it lies; it is not copied to mods-archives.",dropHereX:"Drop — the archives will be unpacked right away",extractSuffix:"Unpack new (taken names — into a suffixed folder)",collisionBan:"Not unpacked — a folder with that name already exists",
dropHint:"Mod archives can simply be dragged into the window — they are copied to the archives folder and unpacked.",dropHere:"Drop — the archives will be added and unpacked",
dropSkipped:"Skipped",dropAddedBusy:"Archives added; unpack them when the current job finishes.",skip_exists:"already there",skip_not_archive:"not an archive",skip_err:"error",skip_incomplete:"interrupted",
"skip_not-archive":"not an archive",
launcherTitle:"SKSE through Steam's Play button",launcherOn:"On: Steam's Play starts SKSE.",launcherOff:"Off: Steam's Play starts the normal launcher.",
launcherHelp:"Replaces SkyrimSELauncher.exe with skse64_loader.exe (the original is kept). Not needed when the setup has Skyrim Runtime Swapper.",launcherSet:"Replace the launcher with SKSE",launcherRestore:"Restore the original launcher",launcherRestoreQ:"Restore the original launcher? Steam's Play will start the game without SKSE.",
gameTitle:"Game",gameDir:"Game folder (where SkyrimSE.exe is)",gameSaved:"Set manually",gameAuto:"Found automatically",gameMissing:"Game folder not found — set it here.",gameAutoBtn:"Find automatically",
skseLog:"Own path to skse64.log",skseLogDef:"default — in the Proton prefix",pDefaultShort:"Default",lootUse:"Sort plugins with LOOT",lootHelp:"Off — own rules only (masters, mod order, collection order).",
nxmPaste:"Paste an nxm:// link (if the browser did not hand it over)",nxmGo:"Download",nxmBad:"A link starting with nxm:// is needed",
collFile:"Attach collection.json",collDetach:"Detach the collection",collNoneQ:"Detach the collection from this profile? Mods and order stay.",collDropQ:"Attach «%s» as the collection of profile «%p»?",collBadFile:"This is not a Vortex/Nexus collection file.",
browse:"Mod browser",fmChoose:"Choose options",fmInstallManual:"Install new ones with choices",fmNone:"(none)",fmCancel:"Cancel",fmNext:"Next",fmClose:"Close",
brSearch:"Mod name on Nexus Mods…",brGo:"Search",brSortEnd:"Most endorsed",brSortDl:"Most downloaded",brSortUpd:"Recently updated",brSortNew:"New",brLoading:"Loading…",
brHint:"Type a name or just press Search.",brKindMods:"Mods",brKindColl:"Collections",brCollSearch:"Search collections",brCollIntro:"Collections are ready-made mod lists with the author\u2019s order and FOMOD choices. Best put into a separate profile: your current one stays as it is.",brCollMods:"mods",brCollNew:"Into a new profile",brCollCur:"Into the current profile",brCollOn:"attached",brCollLogin:"Log in to Nexus (Home) to attach a collection.",brCollPr:"After attaching, the Collection page opens: from there Premium downloads all archives by itself.",brCollFree:"After attaching, the Collection page opens with the list of archives: without Premium each one is downloaded with “Mod Manager Download” on the site.",brCollProfName:"Name of the new profile:",brCollCurQ:"Attach the collection “%s” to the profile “%p”? Mod order and FOMOD choices will come from the collection.",brNothing:"Nothing found.",brFound:"Found",brHidden:"18+ hidden",brHave:"installed",brDownload:"Download",brFiles:"Files",brPage:"Page",brPrev:"Back",brPageOf:"page %p of %n",brNext:"Next",
brIntroPr:"Search Nexus Mods. Premium: the file is downloaded and unpacked here, no website needed.",brIntroFree:"Search Nexus Mods. Without Premium, Nexus allows downloads only through the button on the site: “Via site” opens the right file and its “Mod Manager Download” button hands it to this program (needs the nxm handler in Settings).",
brIntroAnon:"Search Nexus Mods. To download, log in to Nexus in Settings.",brNeedLogin:"Log in to Nexus in Settings (API key).",brFilesOf:"Mod files",brMain:"main",brViaSite:"Via site",
brFreeNote:"Without Premium: the file page opens; press “Mod Manager Download” (or “Slow download”) there. The program receives the nxm:// link and downloads by itself.",brOpenManual:"Open this address in a browser:",brOpened:"opened in the browser",
tagline:"Your world. Your mods. Your story.",log:"Event log",home:"Home",mods:"Mods",fomod:"Packages (FOMOD)",plugins:"Plugin order",deps:"Dependencies",conflicts:"Conflicts",deploy:"Deployment",backups:"Backups",settings:"Settings",
about:"More than a mod manager. Your tool for building a world.",ready:"Ready to play",notdeployed:"Not deployed",other:"Another profile is in the game",pending:"Undeployed changes",working:"Working…",
profile:"Current profile",enabledMods:"Mods enabled",totalSize:"Total size",lastDeploy:"Last deployment",play:"Play",deployPlay:"Deploy and play",playOnly:"Just play",dry:"Dry run (no changes)",
addMods:"Add mods",updCheck:"Check for updates",updCollHint:"If this is a new version of the same mod: after checking for updates, “Unpack new” replaces the old version in place.",updAll:"Update all",updChecked:"Checked",updNever:"Updates not checked yet",updNone:"no new versions",updAvail:"New versions",updFilter:"Has updates",updNeedLogin:"Log in to Nexus (Home) to check mods for updates.",updTitle:"Update",updBtn:"Update",updSite:"Download on the site",updSiteHint:"Without Premium: press “Mod Manager Download” on the site, then “Unpack new” — the new version replaces the old one in place.",updDownloaded:"The new archive is already downloaded — unpack it to replace the old version.",updApply:"Unpack and replace",updOutdated:"The file is marked outdated on Nexus and the author named no replacement — see the mod page.",updGuessed:"The new file was matched by name (the author did not mark it as a replacement) — check it is the same variant.",updKeep:"Order, enabled state and FOMOD choices stay; the old folder and archive go to the trash. Then deploy.",
extract:"Extract new",installFomod:"Install packages (FOMOD)",search:"Search mods…",allCats:"All categories",onlyOff:"Disabled only",onlyOn:"Enabled only",
colName:"Mod name",colCat:"Category",colVer:"Version",colStatus:"Status",active:"Active",off:"Disabled",files:"files",pl:"plugin(s)",
up:"Up",down:"Down",top:"To top (lowest priority)",bottom:"To bottom (highest priority)",openFolder:"Open folder",
cat_skse:"SKSE",cat_ui:"Interface",cat_gfx:"Graphics",cat_audio:"Audio",cat_anim:"Animations",cat_plugin:"Plugins",cat_root:"Game folder",cat_scripts:"Scripts",cat_config:"Configs",cat_other:"Other",
category:"Category",size:"Size",filesCount:"Files",version:"Version",archive:"Archive",nexus:"NexusMods page",dependsOn:"Depends on",neededBy:"Needed by",pluginsL:"Plugins",sksePl:"SKSE plugins",bsa:"BSA archives",
fomodChoice:"FOMOD choice",wins:"Overrides",loses:"Overridden by",depAnalysis:"Dependency analysis",allDepsOk:"All dependencies satisfied",noSel:"Select a mod in the list",order:"Position in order",
pendingBanner:"Mod order or selection changed — the game gets the changes after deployment.",deployNow:"Deploy",
attention:"Need attention (not deployed)",fomodPage:"FOMOD packages of this profile",fomodHint:"“Choose options” opens the installer wizard as in Vortex/MO2: steps, descriptions and option pictures. A new install goes to its own folder and the old one stays until the new one is ready; Cancel changes nothing.",
installedTo:"Installed to",notInstalled:"Not installed",reinstallAuto:"Reinstall all (auto)",modeL:"Mode",
pluginsPage:"Plugin order",poHelp:"Lower in the list loads later and wins when two plugins change the same record (NPC, item, leveled list). The game itself always puts masters (ESM, ESL, ESP with the ESM flag) before regular ESPs and every plugin after its own masters, so the buttons will not let you break these rules.",poAuto:"Order: automatic (LOOT and engine rules)",poCustom:"Order: your own — LOOT does not change it",poDirty:"Unsaved changes",poNotApplied:"Your order is saved but not in plugins.txt yet — it applies when this profile is deployed",poSave:"Save and apply",poCancel:"Discard changes",poReset:"Back to automatic order",poResetQ:"Forget your own order and go back to LOOT/engine rules? Applied right away if the deployment is up to date.",poFilter:"Find a plugin",poMasters:"Masters",poMasterGrp:"Masters (load first)",poRegularGrp:"Regular plugins",poBadMaster:"{0} cannot be above its master {1}",poBadDep:"{0} is a master of {1} and cannot go below it",poGroup:"{0} is a master; the game does not load it among regular ESPs",poGroupR:"{0} is a regular plugin; the game loads it only after all masters",poOffDeps:"Disabled together with {0} — plugins that need it: {1}",poOnMasters:"Enabled together with {0} — its masters: {1}",poMissing:"file not in Data",poOther:"Another profile is deployed now — deploy this one to manage its order.",poPending:"The mod list changed since deployment — the saved order applies on the next deployment.",noDeploy:"Nothing deployed yet.",lootUpdate:"Update LOOT masterlist and check",
depsPage:"Dependencies between mods (by plugin masters)",problems:"Order problems",noProblems:"No problems",
conflictsPage:"File conflicts",recompute:"Recompute",notComputed:"Conflicts not computed yet.",winner:"Wins",loser:"Overridden",count:"Files",dupPlugins:"Same plugin in several mods",identical:"Identical files (not a conflict)",
deployPage:"Deployment",deployBtn:"Deploy profile",undeployBtn:"Full rollback",skseBtn:"Check SKSE",deployedProfile:"Deployed profile",filesDeployed:"Files in the game",incomplete:"Incomplete! Roll back or deploy again.",
skseTitle:"SKSE",gameVer:"Game version",sksePlugins:"SKSE plugins (static)",
backupsPage:"Backups and profile files",backupFiles:"Original game files in backup",changedSets:"Changed files set aside",overwrite:"Profile overwrite (created/changed by the game)",openOw:"Open overwrite",
settingsPage:"Settings",profiles:"Profiles",newProfile:"New profile (empty — you enable the mods)",npCollSrc:"collection.json or a Nexus collection link",npCollPh:"path or link",npFromColl:"Create from collection",
npHint:"A new profile takes only the mod order; all mods are disabled. With a collection — exactly the collection's mods are enabled.",npNeed:"Enter the profile name and the collection file or link.",npFile:"From a collection.json file",npLink:"From a Nexus link",create:"Create",del:"Delete",switchTo:"Switch",paths:"Folders",language:"Language",quit:"Close interface",
busy:"Please wait — another operation is running.",confirmUndeploy:"Remove all mods from the game and restore originals?",confirmDel:"Delete profile «%s»?",profileName:"Profile name",idle:"Ready",
launchFail:"Steam not found to launch the game. Start it from Steam.",gameRunning:"The game is already running.",openArchives:"Open archives folder",noMods:"No mods yet. Put archives into mods-archives and press “Extract new”.",closed:"Interface closed. You can close this tab.",
nexus:"Nexus Mods",nxLogged:"Logged in as",nxPremium:"Premium",nxFree:"free account",nxNone:"Not logged in.",nxKey:"Personal API key",nxKeyHelp:"nexusmods.com → avatar → Site preferences → API Keys → Personal API key. The key is stored only on this computer.",
nxLogin:"Log in",nxLogout:"Log out",nxHandler:"Accept “Mod Manager Download” (nxm://)",nxHandlerHelp:"Without Premium, Nexus allows downloads only through the “Mod Manager Download” button on the site: the file lands in mods-archives.",
nxColl:"Download missing collection archives",nxCollAll:"Download including optional ones",nxDownload:"Download from Nexus (ID or link)",nxAsk:"Mod ID, ID:file ID or the mod page link",nxLoginFail:"Key not accepted (see the log).",
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
function showErr(m){LOGTXT+=(LOGTXT?"\n":"")+m;const pre=document.querySelector("#log");if(pre)pre.textContent=m.split("\n")[0];termPaint()}
const ICONS={home:'<path d="M3 11l9-7 9 7v9h-6v-6H9v6H3z"/>',mods:'<path d="M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z"/>',fomod:'<path d="M12 2l9 5v10l-9 5-9-5V7zM12 12l9-5M12 12v10M12 12L3 7"/>',
plugins:'<path d="M4 6h16M4 12h16M4 18h16"/>',term:'<path d="M4 5h16v14H4zM7 9l3 3-3 3M12 15h5"/>',diag:'<path d="M3 12h4l3-8 4 16 3-8h4"/>',glogs:'<path d="M6 3h9l4 4v14H6zM15 3v4h4M9 11h7M9 15h7M9 19h4"/>',browse:'<path d="M11 4a7 7 0 1 0 0 14 7 7 0 0 0 0-14zM20 20l-4-4M8 11h6M11 8v6"/>',collection:'<path d="M4 4h16v4H4zM6 8v12h12V8M10 12h4"/>',deps:'<path d="M12 3v6M6 21v-6l6-6 6 6v6"/>',conflicts:'<path d="M12 3l10 18H2zM12 10v5M12 18v1"/>',deploy:'<path d="M12 3l8 4-8 4-8-4zM4 12l8 4 8-4M4 17l8 4 8-4"/>',
backups:'<path d="M12 7v5l3 2M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5"/>',settings:'<path d="M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM19.4 15a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.2V21a2 2 0 1 1-4 0v-.1a1.6 1.6 0 0 0-2.7-1.1l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.6 1.6 0 0 0 3.4 14H3a2 2 0 1 1 0-4h.1A1.6 1.6 0 0 0 4.2 7.3l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.6 1.6 0 0 0 9.7 3.4V3a2 2 0 1 1 4 0v.1a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0 1.1 2.7H21a2 2 0 1 1 0 4h-.1a1.6 1.6 0 0 0-1.5 1z"/>'};
function icon(k){return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" stroke-linecap="round">${ICONS[k]}</svg>`}
function avatar(n){let h=0;for(const c of n)h=(h*31+c.charCodeAt(0))>>>0;const hue=h%360;const w=n.replace(/^[^\p{L}\p{N}]+/u,"").split(/\s+/).filter(Boolean);
 const ini=((w[0]||"?")[0]+((w[1]||"")[0]||"")).toUpperCase();return `<div class="av" style="background:linear-gradient(135deg,hsl(${hue} 45% 38%),hsl(${(hue+40)%360} 50% 24%))">${esc(ini)}</div>`}
function fmtSize(b){if(!b)return"0 B";const u=["B","KiB","MiB","GiB","TiB"];let i=0;while(b>=1024&&i<4){b/=1024;i++}return (i?b.toFixed(1):b)+" "+u[i]}
function fmtDate(s){if(!s)return"—";const d=new Date(s);return isNaN(d)?s:d.toLocaleString(L==="uk"?"uk-UA":"en-GB",{dateStyle:"short",timeStyle:"short"})}
function nav(){const c=S.conflicts.pairs_total,pr=S.problems.length;
 const items=[["home",""],["mods",S.mods.length],["browse",""],["fomod",S.fomods.length],["collection",S.collection?S.collection.mods.filter(m=>!m.have&&!m.optional).length||"":""],["plugins",S.deployed?S.deployed.plugins.length:""],["deps",pr?pr:""],["conflicts",c||""],["deploy",""],["backups",(!S.protection.asked||!S.baseline)?"!":""],["diag",""],["glogs",S.log_alert?"!":""],["term",""],["settings",""]];
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
function filtered(){return S.mods.filter(m=>(!q||m.name.toLowerCase().includes(q.toLowerCase()))&&(!cat||(cat==="__on"?m.enabled:cat==="__off"?!m.enabled:cat==="__upd"?m.update&&m.update.status!=="outdated":m.category===cat)))}
const nxUrl=(mod,f)=>`https://www.nexusmods.com/skyrimspecialedition/mods/${+mod}?tab=files&file_id=${+f}&nmm=1`;
function updBar(){if(!S.nexus)return `<span class="m" style="font-size:12.5px">${t("updNeedLogin")}</span>`;const u=S.updates||{},n=u.count||0;
 return `<button class="btn" onclick="run('check_updates')" ${running?"disabled":""}>⟳ ${t("updCheck")}</button>${n&&S.nexus.premium?`<button class="btn pri" onclick="run('update_all')" ${running?"disabled":""}>⬆ ${t("updAll")} (${n})</button>`:""}
 <span class="m" style="font-size:12.5px">${u.checked_at?t("updChecked")+": "+fmtDate(new Date(u.checked_at*1000).toISOString())+(n?` · <a class="link" onclick="cat='__upd';renderMain()">${t("updAvail")}: ${n}</a>`:" · "+t("updNone")):t("updNever")}</span>`}
function updBox(m){const u=m.update;if(!u)return "";
 if(u.status==="outdated")return `<div class="dsec"><h4>${t("updTitle")}</h4><div class="warnline">${t("updOutdated")}</div>${m.nexus_id?`<a class="link" href="https://www.nexusmods.com/skyrimspecialedition/mods/${+m.nexus_id}?tab=files" target="_blank" rel="noopener">↗ ${t("nexus")}</a>`:""}</div>`;
 const pr=S.nexus&&S.nexus.premium,dis=running?" disabled":"";
 const btn=u.status==="downloaded"?`<button class="btn pri"${dis} onclick="run('scan')">${t("updApply")}</button><div class="m" style="font-size:12.5px;margin-top:4px">${t("updDownloaded")}</div>`
  :pr?`<button class="btn pri"${dis} onclick="run('update_mod',${esc(JSON.stringify(m.fomod_of||m.name))})">⬆ ${t("updBtn")}</button>`
  :`<button class="btn pri" onclick="api('/api/open_url',{url:nxUrl(${+u.mod},${+u.target})})">↗ ${t("updSite")}</button><div class="m" style="font-size:12.5px;margin-top:4px">${t("updSiteHint")}</div>`;
 return `<div class="dsec"><h4>${t("updTitle")}</h4><div>${esc(u.cur_version||"?")} → <b>${esc(u.version||"?")}</b>${u.size?` · ${fmtSize(u.size)}`:""}</div>${u.guessed?`<div class="warnline" style="font-size:12.5px">${t("updGuessed")}</div>`:""}<div style="margin-top:6px">${btn}</div><div class="m" style="font-size:12px;margin-top:4px">${t("updKeep")}</div></div>`}
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
 const colBan=(S.collisions||[]).length?`<div class="banner">⚠ ${t("collisionBan")}: ${esc(S.collisions.slice(0,4).join(", "))}${S.collisions.length>4?" …":""} ${S.nexus?`<button class="btn" style="margin-left:auto" onclick="run('check_updates')" title="${t("updCollHint")}">⟳ ${t("updCheck")}</button>`:""}<button class="btn pri" style="${S.nexus?"":"margin-left:auto"}" onclick="run('scan_suffix')">${t("extractSuffix")}</button></div>`:"";
 const dropHint=`<div class="m" style="color:var(--mut);font-size:12.5px;margin:-4px 0 2px;display:flex;gap:14px;flex-wrap:wrap;align-items:center"><span>${t(S.drop_extract?"dropHintX":"dropHint")}</span>
 <label style="display:flex;gap:6px;align-items:center;color:var(--text)"><input type="checkbox" ${S.drop_extract?"checked":""} onchange="api('/api/set',{drop_extract:this.checked}).then(refresh)">${t("dropExtract")}</label></div>`;
 const ban=S.pending&&S.deployed&&S.deployed.profile===S.profile?`<div class="banner">⚠ ${t("pendingBanner")} <button class="btn pri" style="margin-left:auto" onclick="run('deploy')">${t("deployNow")}</button></div>`:"";
 return `${gbBan}${ban}${colBan}<div class="toolbar"><button class="btn pri" id="addBtn">＋ ${t("addMods")}</button><button class="btn" onclick="run('scan')">⤓ ${t("extract")}</button><button class="btn" onclick="run('fomod_auto')">⬢ ${t("installFomod")}</button><button class="btn go" onclick="window._noticeArmed=true;run('auto_all')" ${running?"disabled":""} title="${t("autoAllHint")}">⚡ ${t("autoAll")}</button></div>${dropHint}
 <div class="toolbar"><div class="search">⌕<input id="q" placeholder="${t("search")}" value="${esc(q)}"></div>
 <select id="cat"><option value="">${t("allCats")}</option><option value="__on" ${cat==="__on"?"selected":""}>${t("onlyOn")}</option><option value="__off" ${cat==="__off"?"selected":""}>${t("onlyOff")}</option>${(S.updates||{}).count?`<option value="__upd" ${cat==="__upd"?"selected":""}>${t("updFilter")} (${S.updates.count})</option>`:""}${cats.map(c=>`<option value="${c}" ${cat===c?"selected":""}>${t("cat_"+c)}</option>`).join("")}</select></div>
 <div class="toolbar" style="flex-wrap:wrap;align-items:center">${updBar()}</div>
 <div class="card tablewrap">${S.mods.length?`<table><thead><tr><th class="c-num">#</th><th>${t("colName")}</th><th class="c-cat">${t("colCat")}</th><th class="c-ver">${t("colVer")}</th><th>${t("colStatus")}</th><th></th></tr></thead><tbody>
 ${list.map(m=>`<tr class="row ${sel===m.name?"sel":""}" draggable="true" data-n="${esc(m.name)}"><td style="color:var(--mut)">${m.index+1}</td>
 <td><div class="mname">${avatar(m.name)}<div><div class="n">${esc(m.name)}</div><div class="s">${m.files} ${t("files")}${m.plugins.length?" · "+m.plugins.length+" "+t("pl"):""} · ${esc(m.size||"")}</div></div></div></td>
 <td class="c-cat"><span class="chip c-${m.category}">${t("cat_"+m.category)}</span></td><td class="c-ver">${esc(m.version||"—")}${m.update&&m.update.status!=="outdated"?` <span class="badge up" title="${t("updAvail")}">→ ${esc(m.update.version||"?")}</span>`:m.update?` <span class="badge old" title="${t("updOutdated")}">!</span>`:""}</td>
 <td><div class="st"><button class="sw ${m.enabled?"on":""}" data-sw="${esc(m.name)}"></button><span class="${m.enabled?"on":"off"}">${t(m.enabled?"active":"off")}</span></div></td>
 <td><button class="dots" data-menu="${esc(m.name)}">⋮</button></td></tr>`).join("")}</tbody></table>`:`<div class="empty">${t("noMods")}</div>`}</div>`}
function bindMods(){const qi=$("#q");if(qi)qi.oninput=()=>{q=qi.value;const p=qi.selectionStart;renderMain();const n=$("#q");n.focus();n.setSelectionRange(p,p)};
 const c=$("#cat");if(c)c.onchange=()=>{cat=c.value;renderMain()};const ab=$("#addBtn");if(ab)ab.onclick=addMenu;
 document.querySelectorAll("tr.row").forEach(r=>{r.onclick=ev=>{if(ev.target.closest(".sw,.dots"))return;sel=r.dataset.n;renderMain()};
  r.ondragstart=()=>{dragName=r.dataset.n};r.ondragover=ev=>{ev.preventDefault();r.classList.add("drag-over")};r.ondragleave=()=>r.classList.remove("drag-over");
  r.ondrop=async ev=>{ev.preventDefault();r.classList.remove("drag-over");if(!dragName||dragName===r.dataset.n)return;const to=S.mods.find(m=>m.name===r.dataset.n).index;await move(dragName,to)}});
 document.querySelectorAll("[data-sw]").forEach(b=>b.onclick=async()=>{const m=S.mods.find(x=>x.name===b.dataset.sw);await post("/api/toggle",{name:m.name,enabled:!m.enabled})});
 const os=$("#openSel");if(os)os.onclick=()=>api("/api/open",{what:"mod:"+sel});
 const rs=$("#rmSel");if(rs)rs.onclick=()=>removeDlg(S.mods.find(x=>x.name===sel));
 document.querySelectorAll("[data-menu]").forEach(b=>b.onclick=ev=>{const m=S.mods.find(x=>x.name===b.dataset.menu);
  menu(ev,[[t("up"),()=>move(m.name,m.index-1)],[t("down"),()=>move(m.name,m.index+1)],[t("top"),()=>move(m.name,0)],[t("bottom"),()=>move(m.name,S.mods.length)],[t("openFolder"),()=>api("/api/open",{what:"mod:"+m.name})],[t("rmMod"),()=>removeDlg(m)]])})}
async function post(p,b){const r=await api(p,b);if(r.error==="busy")alert(t("busy"));await refresh()}
async function move(n,to){await post("/api/move",{name:n,to:Math.max(0,to)})}
function detail(){const m=S.mods.find(x=>x.name===sel);if(!m)return `<div class="card detail"><div class="empty">${t("noSel")}</div></div>`;
 const li=a=>a.length?`<ul>${a.map(x=>`<li>${esc(x)}</li>`).join("")}</ul>`:"—";
 const steps=m.fomod_steps?m.fomod_steps.map(s=>s.groups.map(g=>`<li><span style="color:var(--mut)">${esc(String(g.group).replace(/:\s*$/,""))}:</span> ${esc((g.selected||[]).join(", ")||"—")}</li>`).join("")).join(""):"";
 return `<div class="card detail"><div class="dhead">${avatar(m.name)}<div><div class="n">${esc(m.name)}</div><div class="v">${m.version?"v"+esc(m.version):""}</div></div></div>${updBox(m)}
 <div class="dsec"><div class="kv"><span class="k">${t("category")}:</span><span>${t("cat_"+m.category)}</span><span class="k">${t("size")}:</span><span>${esc(m.size)}</span>
 <span class="k">${t("filesCount")}:</span><span>${m.files}</span><span class="k">${t("order")}:</span><span>${m.index+1}</span>${m.archive?`<span class="k">${t("archive")}:</span><span style="word-break:break-all">${esc(m.archive)}</span>`:""}</div>
 ${m.nexus_id?`<div style="margin-top:8px"><a class="link" href="https://www.nexusmods.com/skyrimspecialedition/mods/${m.nexus_id}" target="_blank" rel="noopener">↗ ${t("nexus")}</a></div>`:""}
 <div style="margin-top:6px"><a class="link" id="openSel">📂 ${t("openFolder")}</a></div>
 <div style="margin-top:10px"><button class="btn danger" style="padding:5px 10px" id="rmSel" ${running?"disabled":""}>${t("rmMod")}</button></div></div>
 ${m.plugins.length?`<div class="dsec"><h4>${t("pluginsL")}</h4>${li(m.plugins)}</div>`:""}${m.skse_plugins.length?`<div class="dsec"><h4>${t("sksePl")}</h4>${li(m.skse_plugins)}</div>`:""}
 ${m.bsa.length?`<div class="dsec"><h4>${t("bsa")}</h4>${li(m.bsa)}</div>`:""}${steps?`<div class="dsec"><h4>${t("fomodChoice")}</h4><ul>${steps}</ul></div>`:""}
 <div class="dsec"><h4>${t("depAnalysis")}</h4>${m.deps.length?`<div>${t("dependsOn")}:</div>${li(m.deps)}`:""}${m.rdeps.length?`<div>${t("neededBy")}:</div>${li(m.rdeps)}`:""}
 ${S.problems.some(p=>p.includes("«"+m.name+"»"))?S.problems.filter(p=>p.includes("«"+m.name+"»")).map(p=>`<div class="badline">⚠ ${esc(p)}</div>`).join(""):`<div class="okline">✔ ${t("allDepsOk")}</div>`}</div>
 ${m.wins.length||m.loses.length?`<div class="dsec">${m.wins.length?`<h4>${t("wins")}</h4>${li(m.wins.map(([n,c])=>n+" ("+c+")"))}`:""}${m.loses.length?`<h4>${t("loses")}</h4>${li(m.loses.map(([n,c])=>n+" ("+c+")"))}`:""}</div>`:""}</div>`}
function pageWrap(h){return `<div class="card page">${h}</div>`}
function fomodView(){return pageWrap(`<h2>${t("fomodPage")}</h2><p class="m">${t("fomodHint")}</p><div class="toolbar" style="margin-bottom:12px"><button class="btn pri" onclick="run('fomod_auto')">${t("installFomod")}</button><button class="btn" onclick="run('fomod_redo_auto')">${t("reinstallAuto")}</button><button class="btn" onclick="fomodStart(null,false)">${t("fmInstallManual")}</button></div>
 <div class="grid2">${S.fomods.map(f=>`<div class="box"><b>${esc(f.name)}</b><div style="color:var(--mut);font-size:12.5px;margin:4px 0">${f.installed_to?t("installedTo")+": "+esc(f.installed_to):`<span class="warnline">${t("notInstalled")}</span>`}${f.mode?" · "+t("modeL")+": "+esc(f.mode):""}</div><button class="btn" style="padding:5px 10px;margin:4px 0 6px" ${running?"disabled":""} onclick="fomodStart(${esc(JSON.stringify(f.name))},true)">${t("fmChoose")}</button>
 ${f.steps?`<ul style="margin:0;padding-left:18px;font-size:12.5px">${f.steps.map(s=>s.groups.map(g=>`<li><span style="color:var(--mut)">${esc(String(g.group).replace(/:\s*$/,""))}:</span> ${esc((g.selected||[]).join(", ")||"—")}</li>`).join("")).join("")}</ul>`:""}</div>`).join("")||`<div class="empty">—</div>`}</div>
 ${S.attention.length?`<h2 style="margin-top:18px">${t("attention")}</h2><div class="grid2">${S.attention.map(a=>`<div class="box"><b>${esc(a.name)}</b><div class="warnline" style="font-size:12.5px">${esc(a.layout)}${a.options.length?" — "+esc(a.options.slice(0,6).join(", ")):""}</div></div>`).join("")}</div>`:""}`)}
function collectionView(){const c=S.collection,pr=S.nexus&&S.nexus.premium;
 const fetchBox=S.nexus?`<div class="toolbar" style="margin:10px 0"><div class="search"><input id="collLink" placeholder="${t("collLink")}"></div><button class="btn pri" onclick="const v=$('#collLink').value.trim();v&&run('collection_nexus',v)">${t("collFetch")}</button></div>`:`<p class="m">${t("collNeedLogin")}</p>`;
 const fileBox=`<div class="toolbar" style="margin:10px 0"><button class="btn" onclick="pickCollection()">${t("collFile")}</button>${c?`<button class="btn danger" onclick="confirm(t('collNoneQ'))&&run('collection_none')">${t("collDetach")}</button>`:""}</div>`;
 if(!c)return pageWrap(`<h2>${t("collection")}</h2><p class="m">${t("collNone")}</p>${fileBox}${fetchBox}`);
 const req=c.mods.filter(m=>!m.optional),opt=c.mods.filter(m=>m.optional),miss=c.mods.filter(m=>!m.have);
 return pageWrap(`<h2>${esc(c.name||"?")} <span style="color:var(--mut);font-size:14px">${t("collBy")} ${esc(c.author||"?")}</span></h2>
 <p class="m">${t("collFound")}: ${t("collReq")} ${req.filter(m=>m.have).length}/${req.length}, ${t("collOpt")} ${opt.filter(m=>m.have).length}/${opt.length}</p>
 <div class="toolbar"><button class="btn" onclick="run('scan')">${t("collScan")}</button>${pr&&miss.length?`<button class="btn go" onclick="run('collection_download')">${t("nxColl")}</button><button class="btn" onclick="run('collection_download_all')">${t("nxCollAll")}</button>`:""}</div>
 ${fileBox}${fetchBox}${!pr&&miss.length?`<p class="m">${t("collFreeHint")}</p>`:""}
 ${miss.length?`<h3>${t("collMissing")}: ${miss.length}</h3><table><tbody>${miss.map(m=>`<tr><td>${esc(m.name)} <span style="color:var(--mut)">${esc(m.version||"")}</span>${m.optional?` <span class="chip">${t("optional")}</span>`:""}</td>
 <td style="text-align:right">${m.mod_id&&m.file_id?`<a class="btn" style="padding:4px 10px;white-space:nowrap" target="_blank" rel="noopener" href="https://www.nexusmods.com/skyrimspecialedition/mods/${+m.mod_id}?tab=files&file_id=${+m.file_id}&nmm=1">${t("collDl")}</a>`:"—"}</td></tr>`).join("")}</tbody></table>`:""}`)}
let PO=null,POMSG="",POQ="";
function poItems(){const o=S.plugin_order||{items:[]};return PO||o.items.map(x=>({...x,ms:[...x.ms]}))}
function poFmt(k,...a){return t(k).replace(/\{(\d)\}/g,(_,i)=>a[+i])}
function poMove(i,to){const a=poItems(),it=a[i],rest=a.filter((_,k)=>k!==i),low=x=>x.toLowerCase();
 const nm=rest.filter(x=>x.m).length;let lo=it.m?0:nm,hi=it.m?nm:rest.length,why="";
 rest.forEach((x,k)=>{if(it.ms.some(m=>low(m)===low(x.n))&&k+1>lo){lo=k+1;if(to<lo)why=poFmt("poBadMaster",it.n,x.n)}});
 rest.forEach((x,k)=>{if(x.ms.some(m=>low(m)===low(it.n))&&k<hi){hi=k;if(to>hi)why=poFmt("poBadDep",it.n,x.n)}});
 if(to<lo){if(!why&&!it.m)why=poFmt("poGroupR",it.n);to=lo}
 if(to>hi){if(!why&&it.m)why=poFmt("poGroup",it.n);to=hi}
 rest.splice(to,0,it);PO=rest;POMSG=why;renderMain();const r=document.querySelector(`[data-po="${CSS.escape(it.n)}"]`);if(r)r.scrollIntoView({block:"nearest"})}
function poToggle(i,on){const a=poItems(),low=x=>x.toLowerCase(),it=a[i];it.on=on;PO=a;const ch=[];
 const walk=n=>{for(const x of a){if(on){if(low(x.n)!==low(n))continue;for(const m of x.ms){const y=a.find(z=>low(z.n)===low(m));if(y&&!y.on){y.on=true;ch.push(y.n);walk(y.n)}}}
  else if(x.on&&x.ms.some(m=>low(m)===low(n))){x.on=false;ch.push(x.n);walk(x.n)}}};walk(it.n);
 POMSG=ch.length?poFmt(on?"poOnMasters":"poOffDeps",it.n,ch.join(", ")):"";renderMain()}
function poDirty(){const o=(S.plugin_order||{items:[]}).items;return !!PO&&(PO.length!==o.length||PO.some((x,k)=>x.n!==o[k].n||x.on!==o[k].on))}
function poSave(){const a=poItems();const v=JSON.stringify({order:a.map(x=>x.n),disabled:a.filter(x=>!x.on).map(x=>x.n)});PO=null;POMSG="";run("plugin_order",v)}
function poReset(){if(!confirm(t("poResetQ")))return;PO=null;POMSG="";run("plugin_order","reset")}
function pluginsView(){const d=S.deployed,o=S.plugin_order||{items:[]};
 if(!d||!o.items.length)return pageWrap(`<h2>${t("pluginsPage")}</h2><p class="m">${t("noDeploy")}</p>`);
 const a=poItems(),dirty=poDirty(),q=POQ.trim().toLowerCase(),dis=running?" disabled":"";
 let st=o.custom?t("poCustom"):t("poAuto");const notes=[];
 if(!o.same_profile)notes.push(t("poOther"));else if(o.custom&&o.applied===false)notes.push(S.pending?t("poPending"):t("poNotApplied"));
 const nm=a.filter(x=>x.m).length;let rows="";
 a.forEach((x,i)=>{if(i===0&&x.m)rows+=`<li class="pogrp">${t("poMasterGrp")}</li>`;if(i===nm)rows+=`<li class="pogrp">${t("poRegularGrp")}</li>`;
  if(q&&!x.n.toLowerCase().includes(q))return;
  const b=(x.m?`<span class="badge">${x.l?"ESL":(x.n.toLowerCase().endsWith(".esm")?"ESM":"ESM-flag")}</span>`:(x.l?`<span class="badge">ESL</span>`:""))+(x.miss?` <span class="badge bad">${t("poMissing")}</span>`:"");
  rows+=`<li class="porow${x.on?"":" pooff"}" data-po="${esc(x.n)}"><span class="pon">${i+1}</span><label class="poname"><input type="checkbox" ${x.on?"checked":""}${dis} onchange="poToggle(${i},this.checked)"> <span class="mono">${esc(x.n)}</span> ${b}${x.ms.length?`<span class="m pom">${t("poMasters")}: ${esc(x.ms.join(", "))}</span>`:""}</label><span class="pobtns"><button class="btn sm" title="${t("top")}"${dis} onclick="poMove(${i},0)">⤒</button><button class="btn sm" title="${t("up")}"${dis} onclick="poMove(${i},${i-1})">↑</button><button class="btn sm" title="${t("down")}"${dis} onclick="poMove(${i},${i+1})">↓</button><button class="btn sm" title="${t("bottom")}"${dis} onclick="poMove(${i},${a.length})">⤓</button></span></li>`});
 return pageWrap(`<h2>${t("pluginsPage")}</h2><p class="m">${esc(d.profile)} · ${fmtDate(d.at)} · ${a.length}</p><p class="m">${t("poHelp")}</p>
 <p><b>${dirty?t("poDirty"):st}</b></p>${notes.map(n=>`<p class="m warn">${esc(n)}</p>`).join("")}${POMSG?`<p class="pomsg">${esc(POMSG)}</p>`:""}
 <div class="toolbar" style="margin-bottom:10px;flex-wrap:wrap"><button class="btn pri"${dirty&&!running?"":" disabled"} onclick="poSave()">${t("poSave")}</button><button class="btn"${dirty?"":" disabled"} onclick="PO=null;POMSG='';renderMain()">${t("poCancel")}</button><button class="btn"${o.custom&&!running?"":" disabled"} onclick="poReset()">${t("poReset")}</button><button class="btn"${dis} onclick="run('loot_update')">${t("lootUpdate")}</button></div>
 <div class="search" style="max-width:420px;margin-bottom:8px"><input id="poQ" placeholder="${t("poFilter")}" value="${esc(POQ)}" oninput="POQ=this.value;const p=this.selectionStart;renderMain();const e=$('#poQ');e.focus();e.setSelectionRange(p,p)"></div>
 <ol class="polist">${rows}</ol>`)}
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
 <div class="box"><b>${t("launcherTitle")}</b><div style="margin:6px 0;font-size:13px" class="${S.game.launcher?"okline":""}">${t(S.game.launcher?"launcherOn":"launcherOff")}</div>
 <div style="font-size:12px;color:var(--mut)">${t("launcherHelp")}</div><div class="toolbar" style="margin-top:8px">${S.game.launcher?`<button class="btn" onclick="confirm(t('launcherRestoreQ'))&&run('skse_launcher_restore')">${t("launcherRestore")}</button>`:`<button class="btn pri" onclick="run('skse_launcher')">${t("launcherSet")}</button>`}</div></div>
 ${k?`<div class="box"><b>${t("skseTitle")}</b><div class="kv" style="margin-top:6px"><span class="k">${t("gameVer")}:</span><span>${esc(k.game_version||"?")}</span><span class="k">SKSE:</span><span class="${k.skse&&k.skse.matches_game?"okline":"warnline"}">${k.skse&&k.skse.runtime_dlls&&k.skse.runtime_dlls.length?esc(k.skse.runtime_dlls.join(", ")):"—"}</span>
 <span class="k">${t("sksePlugins")}:</span><span>${Object.entries(cnt).map(([s,n])=>s+": "+n).join(", ")||"—"}</span></div>${(k.warnings||[]).map(w=>`<div class="warnline" style="font-size:12.5px">⚠ ${esc(w)}</div>`).join("")}</div>`:""}</div>`)}
function backupsView(){const b=S.backups,o=S.overwrite,gb=S.game_backups||[];const nm=()=>($("#gbName")?$("#gbName").value.trim():"");
 const setup=!S.protection.asked?protSetup()+"<div style='height:12px'></div>":"";
 const gbox=`<h2>${t("gameBackups")}</h2><p class="m">${t("gbHint")}</p><div class="toolbar" style="margin-bottom:10px"><div class="search" style="flex:1 1 220px;min-width:220px"><input id="gbName" placeholder="${t("gbName")}"></div>
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
function settingsView(){const d=S.deployed;return pageWrap(`<h2>${t("settingsPage")}</h2><div class="grid2" style="grid-template-columns:repeat(auto-fill,minmax(min(100%,340px),1fr))"><div class="box"><b>${t("profiles")}</b><ul style="padding-left:18px">${S.profiles.map(p=>`<li style="margin:6px 0">${esc(p)} ${p===S.profile?"✔":`<button class="btn" style="padding:3px 8px" onclick='run("profile",${JSON.stringify(p)})'>${t("switchTo")}</button>`}
 ${p!==S.profile&&!(d&&d.profile===p)?`<button class="btn danger" style="padding:3px 8px" onclick='confirm(t("confirmDel").replace("%s",${JSON.stringify(p)}))&&run("profile_delete",${JSON.stringify(p)})'>${t("del")}</button>`:""}</li>`).join("")}</ul>
 <div style="margin-top:8px">${t("newProfile")}:</div><div class="toolbar" style="margin-top:6px"><div class="search"><input id="newProf" placeholder="${t("profileName")}"></div><button class="btn pri" onclick="const v=$('#newProf').value.trim();v&&run('profile_new',v)">${t("create")}</button></div>
 <div style="margin-top:10px;font-size:12.5px;color:var(--mut)">${t("npCollSrc")}:</div>
 <div class="toolbar" style="margin-top:4px"><div class="search" style="flex:1 1 100%"><input id="newProfColl" placeholder="${t("npCollPh")}" title="${t("npCollSrc")}"></div><button class="btn" onclick="newProfColl()">${t("npFromColl")}</button></div>
 <div style="font-size:12px;color:var(--mut);margin-top:4px">${t("npHint")}</div></div>
 <div class="box"><b>${t("paths")}</b>
 ${["archives","mods"].map(k=>`<div style="margin-top:10px"><div style="font-size:12.5px;color:var(--mut)">${t(k==="archives"?"pArchives":"pMods")}</div>
 <div class="toolbar" style="margin-top:4px"><div class="search"><input id="p_${k}" value="${esc(S.paths[k])}"></div></div>
 <div class="toolbar" style="margin-top:6px"><button class="btn pri" style="padding:5px 10px" onclick="setDir('${k}',false)">${t("pSave")}</button><button class="btn" style="padding:5px 10px" onclick="setDir('${k}',true)">${t("pSaveMove")}</button>
 <button class="btn" style="padding:5px 10px" onclick="run('set_${k}','default')">${t("pDefault")}</button><a class="link" onclick="api('/api/open',{what:'${k}'})">📂</a></div></div>`).join("")}
 <div style="font-size:12px;color:var(--mut);margin-top:8px">${t("pHint")}</div>
 <div class="kv mono" style="margin-top:10px">${["root","output","profile"].map(k=>`<span class="k">${k}</span><a class="link" style="word-break:break-all" onclick="api('/api/open',{what:'${k==="root"?"output":k}'})">${esc(S.paths[k])}</a>`).join("")}</div></div>
 <div class="box"><b>${t("gameTitle")}</b>
 <div style="margin-top:8px;font-size:12.5px;color:var(--mut)">${t("gameDir")}</div><div class="toolbar" style="margin-top:4px"><div class="search"><input id="gDir" value="${esc(S.game.saved||S.game.dir||"")}" placeholder="…/Skyrim Special Edition"></div></div>
 <div style="font-size:12.5px;margin-top:4px" class="${S.game.dir?"okline":"badline"}">${S.game.dir?(S.game.saved?t("gameSaved"):t("gameAuto"))+": "+esc(S.game.dir):t("gameMissing")}</div>
 <div class="toolbar" style="margin-top:6px"><button class="btn pri" style="padding:5px 10px" onclick="const v=$('#gDir').value.trim();v&&run('set_game',v)">${t("pSave")}</button><button class="btn" style="padding:5px 10px" onclick="run('set_game','default')">${t("gameAutoBtn")}</button></div>
 <div style="margin-top:12px;font-size:12.5px;color:var(--mut)">${t("skseLog")}</div><div class="toolbar" style="margin-top:4px"><div class="search"><input id="sLog" value="${esc(S.game.skse_log||"")}" placeholder="${t("skseLogDef")}"></div></div>
 <div class="toolbar" style="margin-top:6px"><button class="btn pri" style="padding:5px 10px" onclick="const v=$('#sLog').value.trim();v&&run('set_skse_log',v)">${t("pSave")}</button><button class="btn" style="padding:5px 10px" onclick="run('set_skse_log','default')">${t("pDefaultShort")}</button></div>
 <label style="display:flex;gap:8px;align-items:flex-start;margin-top:12px;font-size:13px"><input type="checkbox" ${S.game.loot?"checked":""} onchange="run(this.checked?'loot_on':'loot_off')"><span>${t("lootUse")}<br><span style="color:var(--mut);font-size:12px">${t("lootHelp")}</span></span></label></div>
 <div class="box"><b>${t("nexus")}</b><div style="margin:6px 0">${S.nexus?`${t("nxLogged")} <b>${esc(S.nexus.name)}</b> (${t(S.nexus.premium?"nxPremium":"nxFree")})`:t("nxNone")}</div>
 ${S.nexus?`<button class="btn" onclick="nxLogout()">${t("nxLogout")}</button>`:`<div style="font-size:12.5px;color:var(--mut)">${t("nxKeyHelp")}</div><div class="toolbar" style="margin-top:6px"><div class="search"><input id="nxKey" type="password" placeholder="${t("nxKey")}"></div><button class="btn pri" onclick="nxLogin()">${t("nxLogin")}</button></div>`}
 <div style="margin-top:10px"><button class="btn" onclick="run('nxm_handler')">${t("nxHandler")}</button><div style="font-size:12px;color:var(--mut);margin-top:4px">${t("nxHandlerHelp")}</div></div>
 ${S.nexus?`<div style="margin-top:10px;font-size:12.5px;color:var(--mut)">${t("nxmPaste")}</div><div class="toolbar" style="margin-top:4px"><div class="search" style="flex:1 1 100%"><input id="nxmUrl" placeholder="nxm://…"></div><button class="btn" onclick="const v=$('#nxmUrl').value.trim();v.startsWith('nxm://')?run('nxm',v):alert(t('nxmBad'))">${t("nxmGo")}</button></div>`:""}
 ${S.nexus&&S.nexus.premium?`<div class="toolbar" style="margin-top:10px"><button class="btn" onclick="nxDl()">${t("nxDownload")}</button>${S.collection?`<button class="btn" onclick="run('collection_download')">${t("nxColl")}</button><button class="btn" onclick="run('collection_download_all')">${t("nxCollAll")}</button>`:""}</div>`:""}</div>
 <div class="box"><b>${t("language")}</b><div style="margin-top:8px"><select onchange="run('lang',this.value)"><option value="uk" ${L==="uk"?"selected":""}>Українська</option><option value="en" ${L==="en"?"selected":""}>English</option></select></div>
 <div style="margin-top:14px"><button class="btn danger" onclick="quit()">${t("quit")}</button></div></div></div>`)}
function bindBackups(){document.querySelectorAll("[data-gb]").forEach(b=>b.onclick=()=>{const n=b.dataset.n,k=b.dataset.gb;
 if(k==="restore"&&!confirm(t("gbConfirm").replace("%s",n)))return;if(k==="delete"&&!confirm(t("gbDelConfirm").replace("%s",n)))return;
 run(k==="restore"?"restore_game":k==="verify"?"backup_verify":"backup_delete",n)})}
/* ---- result of the one-button setup ---- */
let NOTICE_SEEN=0;
function showNotice(n){if(!n||n.id<=NOTICE_SEEN)return;if(!NOTICE_SEEN&&!running&&!window._noticeArmed){NOTICE_SEEN=n.id;return}NOTICE_SEEN=n.id;
 const w=(n.warnings||[]).map(x=>`<li>${esc(x)}</li>`).join("")+(n.more?`<li>… +${n.more}</li>`:"");
 dlg("files",`<div class="dh"><b class="${n.ok?(n.no_skse?"warnline":"okline"):"badline"}" style="font-size:18px">${n.ok?(n.no_skse?"⚠ "+t("autoDoneNoSkse"):"✔ "+t("autoDone")):"✖ "+t("autoFail")}</b></div>
  <div class="db">${n.ok?`<p>${t("autoDoneText")}</p>`:`<p>${t("autoFailText")}</p>`}${w?`<p class="warnline">${t("autoWarn")}:</p><ul class="mono" style="font-size:12px">${w}</ul>`:""}</div>
  <div class="df">${!n.ok||w?`<button class="btn" id="ntLog">${t("term")}</button>`:""}<button class="btn pri" id="ntOk">OK</button></div>`);
 $("#ntOk").onclick=closeDlg;const l=$("#ntLog");if(l)l.onclick=()=>{closeDlg();PROBONLY=!n.ok;page="term";render()}}
/* ---- removing a mod ---- */
function removeDlg(m){if(!m)return;if(running){alert(t("busy"));return}
 const base=m.fomod_of||m.name,folders=S.mods.filter(x=>x.name===base||x.fomod_of===base).map(x=>x.name);if(m.fomod_of&&!folders.includes(base))folders.unshift(base);
 const inGame=S.deployed&&m.enabled&&S.deployed.profile===S.profile;
 const o=dlg("files",`<div class="dh"><b>${t("rmMod")}: ${esc(base)}</b></div><div class="db">
  <p>${t("rmFolders")}:</p><ul class="mono">${[...new Set(folders)].map(f=>`<li>${esc(f)}</li>`).join("")}</ul>
  ${m.archive?`<label style="display:flex;gap:8px;align-items:flex-start;margin:10px 0"><input type="checkbox" id="rmArc" checked><span>${t("rmArchive")}: <span class="mono">${esc(m.archive)}</span><br><span style="color:var(--mut);font-size:12.5px">${t("rmKeepNote")}</span></span></label>`:`<p class="m" style="color:var(--mut)">${t("rmNoArchive")}</p>`}
  <p class="m" style="color:var(--mut)">${t("rmTrash")}</p>${inGame?`<p class="warnline">⚠ ${t("rmInGame")}</p>`:""}</div>
  <div class="df"><button class="btn" id="rmNo">${t("fmCancel")}</button><button class="btn danger" id="rmYes">${t("rmMod")}</button></div>`);
 $("#rmNo").onclick=closeDlg;$("#rmYes").onclick=async()=>{const a=$("#rmArc");closeDlg();await run(a&&!a.checked?"remove_mod_keep":"remove_mod",base)}}
/* ---- home: what this setup is and what needs attention ---- */
function homeView(){const on=S.mods.filter(m=>m.enabled),d=S.deployed,c=S.collection,g=S.game||{},k=S.skse,pr=S.protection||{},gb=S.game_backups||[];
 const ok=v=>`<span class="okline">✔</span>`,warn=v=>`<span class="warnline">⚠</span>`,bad=v=>`<span class="badline">✖</span>`;
 const row=(st,label,val,go)=>`<div class="hrow">${st}<span class="hk">${label}</span><span class="hv">${val}</span>${go?`<a class="link" onclick="page='${go}';render()">›</a>`:""}</div>`;
 const missReq=c?c.mods.filter(m=>!m.have&&!m.optional).length:0;
 const nx=S.nexus?`<div class="box hnx"><b>Nexus Mods</b><div style="margin:8px 0">${ok()} ${t("nxLogged")} <b>${esc(S.nexus.name)}</b> · ${t(S.nexus.premium?"nxPremium":"nxFree")}</div>
  <div class="toolbar"><button class="btn" onclick="nxLogout()">${t("nxLogout")}</button></div></div>`
  :`<div class="box hnx" style="border-color:var(--acc)"><b>Nexus Mods</b><p class="m" style="margin:6px 0 10px">${t("hmNxWhy")}</p>
  <div class="toolbar"><div class="search" style="flex:1 1 220px"><input id="nxKeyHome" type="password" autocomplete="off" placeholder="${t("nxKey")}"></div><button class="btn pri" onclick="nxLogin('nxKeyHome')">${t("nxLogin")}</button></div>
  <div style="font-size:12.5px;color:var(--mut);margin-top:8px">${t("nxKeyHelp")} <a class="link" onclick="api('/api/open_url',{url:'https://next.nexusmods.com/settings/api-keys'})">${t("hmKeyPage")}</a></div></div>`;
 const setup=`<div class="box"><b>${t("hmSetup")}</b>
  ${row("•",t("profile"),esc(S.profile)+(S.profiles.length>1?` <span style="color:var(--mut)">(${S.profiles.length} ${t("hmProfiles")})</span>`:""),"settings")}
  ${row(c?(missReq?warn():ok()):"•",t("collection"),c?`${esc(c.name||"?")} <span style="color:var(--mut)">${t("collBy")} ${esc(c.author||"?")}</span>${missReq?` · <span class="warnline">${t("collMissing")}: ${missReq}</span>`:""}`:`<span style="color:var(--mut)">${t("hmNoColl")}</span>`,"collection")}
  ${row("•",t("enabledMods"),`${on.length} / ${S.mods.length} · ${fmtSize(on.reduce((a,m)=>a+(m.size_bytes||0),0))}`,"mods")}
  ${row("•",t("fomod"),String(S.fomods.length),"fomod")}</div>`;
 const game=`<div class="box"><b>${t("hmGame")}</b>
  ${row(g.dir?ok():bad(),t("hmGameDir"),g.dir?esc(g.dir):t("gameMissing"),"settings")}
  ${row("•",t("gameVer"),gameVerText())}
  ${row(k?(k.skse&&k.skse.matches_game?ok():warn()):"•","SKSE",k?(k.skse&&k.skse.matches_game?t("hmSkseOk"):t("hmSkseBad")):t("dgNotChecked"),"deploy")}
  ${row(!d?"•":(d.profile!==S.profile||S.pending||!d.complete)?warn():ok(),t("lastDeploy"),d?`${fmtDate(d.at)} · ${esc(d.profile)}${!d.complete?` · <span class="badline">${t("incomplete")}</span>`:S.pending&&d.profile===S.profile?` · <span class="warnline">${t("pending")}</span>`:""}`:t("notdeployed"),"deploy")}
  ${row("•",t("launcherTitle"),t(g.launcher?"launcherOn":"launcherOff"),"deploy")}</div>`;
 const prot=`<div class="box"><b>${t("protect")}</b>
  ${row(S.baseline?ok():warn(),t("prList"),S.baseline?`${esc(S.baseline.version)} · ${S.baseline.files} ${t("files")}`:t("dgNo"),"backups")}
  ${row(pr.incremental?ok():"•",t("prInc"),pr.incremental?`${pr.store_files||0} · ${fmtSize(pr.store_size||0)}`:t("prOff"),"backups")}
  ${row(gb.length?ok():"•",t("gameBackups"),gb.length?`${gb.length} · ${esc(gb[0].name)}`:t("gbNone"),"backups")}</div>`;
 const att=[];if(S.problems.length)att.push(row(bad(),t("deps"),`${S.problems.length}`,"deps"));
 if(S.conflicts.pairs_total)att.push(row(warn(),t("conflicts"),`${S.conflicts.pairs_total}`,"conflicts"));
 if(S.attention.length)att.push(row(warn(),t("attention"),`${S.attention.length}`,"fomod"));
 if((S.collisions||[]).length)att.push(row(warn(),t("collisionBan"),`${S.collisions.length}`,"mods"));
 if(S.overwrite.total)att.push(row("•",t("overwrite"),`${S.overwrite.total} ${t("files")}`,"backups"));
 const attn=`<div class="box"><b>${t("hmAttention")}</b>${att.length?att.join(""):`<div style="margin-top:8px">${ok()} ${t("hmAllGood")}</div>`}</div>`;
 const quick=`<div class="toolbar" style="margin-top:12px"><button class="btn pri" id="hmAdd">＋ ${t("addMods")}</button><button class="btn go" onclick="window._noticeArmed=true;run('auto_all')" ${running?"disabled":""} title="${t("autoAllHint")}">⚡ ${t("autoAll")}</button></div>
  <div style="font-size:12.5px;color:var(--mut);margin-top:6px">⚡ ${t("autoAllHint")}</div>`;
 return pageWrap(`<h2>${t("home")}</h2><p class="m">${t("hmIntro")}</p>${quick}<div class="grid2" style="margin-top:12px;grid-template-columns:repeat(auto-fill,minmax(min(100%,330px),1fr))">${nx}${setup}${game}${prot}${attn}</div>`)}
function addMenu(ev){menu(ev,[[t("browse")+" (Nexus)",()=>{page="browse";render()}],[t("pickArchives"),pickArchives],[t("openArchives"),()=>api("/api/open",{what:"archives"})],[t("extract"),()=>run("scan")],[t("extractSuffix"),()=>run("scan_suffix")]]
 .concat(S.nexus&&S.nexus.premium?[[t("nxDownload"),nxDl]]:[]).concat(S.nexus&&S.nexus.premium&&S.collection?[[t("nxColl"),()=>run("collection_download")]]:[]))}
function gameVerText(){const g=S.game||{},r=g.srs,k=S.skse,exe=g.exe_version||(k&&k.game_version)||(S.baseline&&S.baseline.version)||"?";
 if(!r)return esc(exe);const same=(a,b)=>String(a||"").split(".").slice(0,3).join(".")===String(b||"").split(".").slice(0,3).join(".");
 return `Steam: <b>${esc(r.source)}</b> → ${t("srsFor")} <b>${esc(r.target)}</b><br><span style="color:var(--mut)">Skyrim Runtime Swapper${r.deployed?"":" ("+t("srsNotDeployed")+")"} · SkyrimSE.exe: ${esc(exe)}${same(exe,r.target)?" ("+t("srsSwapped")+")":""}</span>`}
function bindHome(){const a=$("#hmAdd");if(a)a.onclick=addMenu;
 const k=$("#nxKeyHome");if(k)k.onkeydown=e=>{if(e.key==="Enter")nxLogin("nxKeyHome")}}
/* ---- event log and terminal ---- */
let LOGTXT="",PART="",TERMVAL="",HIST=[],HI=-1;
try{HIST=JSON.parse(localStorage.getItem("smeHist")||"[]")}catch(e){}
function probBadge(){let b=$("#probCnt");if(!NPROB){if(b)b.remove();return}if(!b){b=document.createElement("span");b.id="probCnt";b.className="pc";b.onclick=()=>{PROBONLY=true;page="term";render()};
 document.querySelector(".log .rt").prepend(b)}b.textContent="⚠ "+NPROB;b.title=t("logProblems")}
function termJob(){return !!(S&&S.job&&S.job.running&&S.job.term)}
function termView(){const tj=termJob(),busy=running&&!tj;
 const ex=["--help","--profiles","--skse","--conflicts","--deploy --dry-run","--game-check","--backups","--nexus-status","--fomod-redo"];
 return pageWrap(`<div class="termpage"><h2>${t("term")}</h2><p class="m">${t("termHint")}</p>
 <div class="chips">${ex.map(x=>`<button data-ex="${esc(x)}">${esc(x)}</button>`).join("")}</div>
 <pre class="termout" id="termOut"></pre>
 <div class="termin"><span class="ps">${tj?"›":"sme $"}</span><input id="termIn" autocomplete="off" spellcheck="false" placeholder="${t(tj?"termAnswer":busy?"busy":"termPh")}" ${busy?"disabled":""}>
 <button class="btn pri" id="termGo" ${busy?"disabled":""}>${t(tj?"termSend":"termRun")}</button>${tj?`<button class="btn danger" id="termStop">${t("termStop")} (Ctrl+C)</button>`:""}
 <button class="btn" id="termKonsole" title="${t("termKonsoleHint")}">${t("termKonsole")}</button><button class="btn" id="termClear">${t("termClear")}</button><button class="btn" id="termCopy">${t("dgCopy").split(" ")[0]}</button></div>
 <div class="toolbar" style="margin-top:8px;flex-wrap:wrap;font-size:12.5px;color:var(--mut)"><label style="display:flex;gap:6px;align-items:center"><input type="checkbox" id="termProb" ${PROBONLY?"checked":""}>${t("logProblems")}</label>
 <a class="link" id="logOpen">${t("logOpen")}</a><a class="link" id="logSave">${t("logSave")}</a><span class="mono" style="word-break:break-all">${esc(LOGPATH)}</span></div></div>`)}
const PROB_RE=/^(?!\s*ERRORS?:\s*$).*(ERROR|ПОМИЛКА|CRITICAL|КРИТИЧНО|Traceback|NOT activated|НЕ активовано|FAIL|Could not read contents|Не вдалося прочитати вміст)/,WARN_RE=/WARNING|УВАГА|⚠/;
let PROBONLY=false,LOGPATH="",NPROB=0,LASTPROB="";
let TBK=false;  // inside a Python traceback: the filter keeps the whole block, down to the exception line
function keepLine(l){if(l.startsWith("Traceback")){TBK=true;return true}if(TBK){if(!/^\s/.test(l))TBK=false;return true}return PROB_RE.test(l)||WARN_RE.test(l)||l.startsWith("$ ")}
function termPaint(){const o=$("#termOut");if(!o)return;const end=o.scrollTop+o.clientHeight>=o.scrollHeight-30;
 const lines=LOGTXT.split("\n");TBK=false;const sel=PROBONLY?lines.filter(keepLine):lines;
 o.innerHTML=sel.map(l=>PROB_RE.test(l)?`<span class="bad">${esc(l)}</span>`:WARN_RE.test(l)?`<span class="warn">${esc(l)}</span>`:esc(l)).join("\n");
 if(PART){const sp=document.createElement("span");sp.className="part";sp.textContent=(LOGTXT?"\n":"")+PART;o.appendChild(sp)}if(end)o.scrollTop=o.scrollHeight}
const lineHtml=l=>PROB_RE.test(l)?`<span class="bad">${esc(l)}</span>`:WARN_RE.test(l)?`<span class="warn">${esc(l)}</span>`:esc(l);
function termAdd(lines){const o=$("#termOut");if(!o)return;const end=o.scrollTop+o.clientHeight>=o.scrollHeight-30;const old=o.querySelector(".part");if(old)old.remove();
 const sel=PROBONLY?lines.filter(keepLine):lines;
 if(sel.length)o.insertAdjacentHTML("beforeend",(o.childNodes.length?"\n":"")+sel.map(lineHtml).join("\n"));
 if(PART){const sp=document.createElement("span");sp.className="part";sp.textContent="\n"+PART;o.appendChild(sp)}if(end)o.scrollTop=o.scrollHeight}
let LOADING_LOG=false;
async function loadLogFile(){if(LOADING_LOG)return;LOADING_LOG=true;try{
 // Snapshot first: file size and the matching log position. The file is read only up to that size and the
 // poll continues right after it, so no line is shown twice and none is missed.
 const h=await api("/api/logfile?from=999999999999");const TAIL=2*1024*1024,end=h.size||0;let from=Math.max(0,end-TAIL),txt="";
 for(let i=0;i<8&&from<end;i++){const r=await api(`/api/logfile?from=${from}&to=${end}`);let tx=r.text||"";if(i===0&&from>0)tx=tx.slice(tx.indexOf("\n")+1);
  txt+=tx;LOGPATH=r.path||LOGPATH;if(!r.text)break;from=r.next}
 if(end>TAIL)txt=t("logTail")+"\n"+txt;
 LOGTXT=txt.replace(/\n$/,"");if(typeof h.log_next==="number")logFrom=h.log_next;
 if(page==="term"){termPaint();const o=$("#termOut");if(o)o.scrollTop=o.scrollHeight;const lp=document.querySelector(".termpage .mono");if(lp)lp.textContent=LOGPATH}
 }finally{LOADING_LOG=false}}
async function termSubmit(){const i=$("#termIn");const v=i.value;if(!v.trim()&&!termJob())return;
 if(termJob()){const r=await api("/api/term_input",{text:v});i.value="";TERMVAL="";if(r.error)alert(r.error);return}
 if(v.trim()&&HIST[HIST.length-1]!==v){HIST.push(v);HIST=HIST.slice(-100);try{localStorage.setItem("smeHist",JSON.stringify(HIST))}catch(e){}}HI=-1;
 const r=await api("/api/term",{cmd:v});if(r.error){alert(r.error==="busy"?t("busy"):r.error==="options-only"?t("termOnlyOpts"):r.error==="no-gui"?t("termNoGui"):r.error);return}
 i.value="";TERMVAL="";running=true;S.job={running:true,term:true,args:[]};jobLine();renderMain()}
function bindTerm(){termPaint();const to=$("#termOut");if(to)to.scrollTop=to.scrollHeight;loadLogFile();
 $("#termProb").onchange=e=>{PROBONLY=e.target.checked;termPaint()};
 $("#logOpen").onclick=async()=>{const r=await api("/api/log_open",{});if(r.result!=="ok")alert(LOGPATH)};
 $("#logSave").onclick=async()=>{const r=await api("/api/log_save",{});alert(r.path?t("logSaved")+"\n"+r.path:r.error)};const i=$("#termIn");i.value=TERMVAL;i.oninput=()=>{TERMVAL=i.value};if(!i.disabled)i.focus();
 i.onkeydown=e=>{if(e.key==="Enter"){e.preventDefault();termSubmit()}
  else if(e.key==="ArrowUp"&&!termJob()&&HIST.length){e.preventDefault();HI=HI<0?HIST.length-1:Math.max(0,HI-1);i.value=TERMVAL=HIST[HI]}
  else if(e.key==="ArrowDown"&&!termJob()&&HI>=0){e.preventDefault();HI++;if(HI>=HIST.length){HI=-1;i.value=TERMVAL=""}else i.value=TERMVAL=HIST[HI]}
  else if(e.key==="c"&&e.ctrlKey&&termJob()&&i.selectionStart===i.selectionEnd){e.preventDefault();api("/api/term_stop",{})}};
 $("#termGo").onclick=termSubmit;const st=$("#termStop");if(st)st.onclick=()=>api("/api/term_stop",{});
 $("#termClear").onclick=()=>{LOGTXT="";termPaint()};
 $("#termKonsole").onclick=async()=>{const r=await api("/api/open_terminal",{});if(r.result==="none")alert(t("termNoTerm"))};
 $("#termCopy").onclick=async()=>{try{await navigator.clipboard.writeText(LOGTXT);$("#termCopy").textContent="✔"}catch(e){prompt("",LOGTXT.slice(-20000))}};
 document.querySelectorAll("[data-ex]").forEach(b=>b.onclick=()=>{i.value=TERMVAL=b.dataset.ex;i.focus()})}
/* ---- game and mod logs ---- */
let GL=null,GLSEL="",GLTXT=null,GLQ="",GLPROB=false,GLGRP="";
const GL_ERR=/\[(?:error|critical|E|C)\]|\b(?:error|errors|exception|failed|failure|fatal|crash(?:ed)?|unable to|cannot|can't|could not|couldn't|missing|not found)\b/i,GL_WARN=/\[(?:warning|warn|W)\]|\bwarn(?:ing)?s?\b/i,GL_NOISE=/\b0 errors?\b|\berrors?: 0\b|no errors?\b/i;
const GL_TAG=/\[(error|critical|err|E|C|warning|warn|W|info|debug|trace|I|D|T)\]/i;
const glLv=l=>{const m=GL_TAG.exec(l);if(m){const g=m[1].toLowerCase();return ["error","critical","err","e","c"].includes(g)?"e":["warning","warn","w"].includes(g)?"w":""}if(GL_NOISE.test(l))return "";const e=l.search(GL_ERR),w=l.search(GL_WARN);return e>=0&&w>=0?(e<w?"e":"w"):e>=0?"e":w>=0?"w":""};
const GLG={crash:"Crash",skse:"SKSE",papyrus:"Papyrus",data:"Data/SKSE",game:"Game",proton:"Proton"};
const glDate=x=>fmtDate(new Date(x*1000).toISOString());
function glPaint(){const o=$("#glOut");if(!o)return;if(GLTXT===null){o.innerHTML=`<span class="m">${t(GLSEL?"glLoading":"glSel")}</span>`;return}
 const q=GLQ.trim().toLowerCase();let lines=GLTXT.text.split(/\r?\n/);if(GLPROB)lines=lines.filter(l=>glLv(l));if(q)lines=lines.filter(l=>l.toLowerCase().includes(q));
 const hl=h=>{if(!q)return h;const i=h.toLowerCase().indexOf(esc(q).toLowerCase());return i<0?h:h.slice(0,i)+"<mark>"+h.slice(i,i+esc(q).length)+"</mark>"+h.slice(i+esc(q).length)};
 o.innerHTML=(GLTXT.cut&&!q&&!GLPROB?`<span class="m">${t("glCut")}</span>\n`:"")+(lines.length?lines.map(l=>{const v=glLv(l),h=hl(esc(l));return v==="e"?`<span class="bad">${h}</span>`:v==="w"?`<span class="warn">${h}</span>`:h}).join("\n"):`<span class="m">${t("glNoMatch")}</span>`)}
async function glLoad(path){GLSEL=path;GLTXT=null;renderMain();const r=await api("/api/gamelog?path="+encodeURIComponent(path));if(GLSEL!==path)return;GLTXT=r.error?{text:String(r.error),cut:false}:r;
 if(page==="glogs"){glPaint();const cp=$("#glCopy");if(cp)cp.disabled=false;const o=$("#glOut");if(o)o.scrollTop=GLQ||GLPROB?0:o.scrollHeight}}
function glogsView(){if(!GL)return pageWrap(`<h2>${t("glogs")}</h2><div class="empty">${t("glLoading")}</div>`);
 if(GL.no_game)return pageWrap(`<h2>${t("glogs")}</h2><p class="m">${t("glNoGame")}</p>`);
 const c=GL.last_crash,cs=c&&c.crash||{},dis=running?" disabled":"";
 const crash=c?`<div class="box glcrash"><b>💥 ${t("glCrash")}: <span class="mono">${esc(c.name)}</span></b> <span class="m">${glDate(c.mtime)}${c.fresh?" · "+t("glCrashFresh"):""}</span>
  ${cs.exception?`<div class="mono" style="margin-top:6px;overflow-wrap:anywhere">${esc(cs.exception)}</div>`:""}
  ${cs.mentions&&cs.mentions.length?`<div class="m" style="margin-top:6px">${t("glMentions")}:</div><div class="chips" style="margin-top:4px">${cs.mentions.map(m=>`<span class="chip">${esc(m.name)} ×${m.count}</span>`).join("")}</div>`:""}
  <div style="margin-top:8px"><button class="btn" data-glsel="${esc(c.path)}">${t("glOpen")}</button></div></div>`:"";
 const pap=GL.papyrus_on===null||GL.papyrus_on===undefined?`<span class="m">${t("glPapNo")}</span>`:`<span class="${GL.papyrus_on?"okline":"m"}">${t(GL.papyrus_on?"glPapOn":"glPapOff")}</span> <button class="btn sm"${dis} title="${esc(t("glPapHint"))}" onclick="run('${GL.papyrus_on?"papyrus_off":"papyrus_on"}')">${t(GL.papyrus_on?"glPapDisable":"glPapEnable")}</button>`;
 const groups=[...new Set(GL.files.map(f=>f.group))];
 const files=GL.files.filter(f=>!GLGRP||f.group===GLGRP);
 const list=GL.files.length?`<div class="chips" style="margin-bottom:6px"><button class="${GLGRP?"":"on"}" data-glg="">${t("glGroups")} (${GL.files.length})</button>${groups.map(g=>`<button class="${GLGRP===g?"on":""}" data-glg="${g}">${GLG[g]||g} (${GL.files.filter(f=>f.group===g).length})</button>`).join("")}</div>
  <div class="gllist">${files.map(f=>`<div class="glrow${f.path===GLSEL?" on":""}" data-glsel="${esc(f.path)}" title="${esc(f.path)}"><span class="badge">${GLG[f.group]||f.group}</span><span class="mono glname">${esc(f.name)}</span>
  ${!f.fresh&&GL.session?`<span class="badge old" title="${esc(t("glOldHint"))}">${t("glOld")}</span>`:""}${f.errors?`<span class="cnt bad" title="${t("glErr")}">✖ ${f.errors}</span>`:""}${f.warnings?`<span class="cnt wn" title="${t("glWarn")}">⚠ ${f.warnings}</span>`:""}<span class="m glmeta">${glDate(f.mtime)} · ${fmtSize(f.size)}</span></div>`).join("")}</div>`:`<p class="m">${t("glNone")}</p>`;
 const viewer=`<div class="toolbar" style="margin:10px 0 6px;flex-wrap:wrap"><div class="search" style="flex:1 1 220px;min-width:200px;max-width:360px"><input id="glQ" placeholder="${t("glFind")}" value="${esc(GLQ)}"></div>
  <label style="display:flex;gap:6px;align-items:center;font-size:13px"><input type="checkbox" id="glProb" ${GLPROB?"checked":""}>${t("glProb")}</label>
  <button class="btn" id="glRef">${t("glRefresh")}</button><button class="btn" id="glFile"${GLSEL?"":" disabled"}>${t("glOpenFile")}</button><button class="btn" id="glDir"${GLSEL?"":" disabled"}>${t("glOpenDir")}</button><button class="btn" id="glCopy"${GLTXT?"":" disabled"}>${t("glCopy")}</button></div>
  ${GLSEL?`<div class="mono m" style="font-size:12px;overflow-wrap:anywhere">${esc(GLSEL)}</div>`:""}<pre class="termout glout" id="glOut"></pre>`;
 return pageWrap(`<h2>${t("glogs")}</h2><p class="m">${t("glIntro")}</p>${crash}<p style="margin:8px 0">${pap}</p>${list}${viewer}`)}
async function bindGlogs(){if(!GL){const r=await api("/api/gamelogs");GL=r;if(!GLSEL&&GL.files&&GL.files.length){const f=(GL.last_crash&&GL.last_crash.fresh)?GL.last_crash:GL.files.find(x=>x.errors&&x.fresh)||GL.files[0];GLSEL=f.path;GLTXT=null}if(page==="glogs"){renderMain();if(GLSEL)glLoad(GLSEL)}return}
 glPaint();const o=$("#glOut");if(o&&GLTXT&&!GLQ&&!GLPROB)o.scrollTop=o.scrollHeight;
 document.querySelectorAll("[data-glsel]").forEach(e=>e.onclick=async()=>{await glLoad(e.dataset.glsel);const v=$("#glQ");if(v)v.scrollIntoView({block:"start",behavior:"smooth"})});
 document.querySelectorAll("[data-glg]").forEach(e=>e.onclick=()=>{GLGRP=e.dataset.glg;renderMain()});
 const qi=$("#glQ");qi.oninput=()=>{GLQ=qi.value;glPaint()};$("#glProb").onchange=e=>{GLPROB=e.target.checked;glPaint()};
 $("#glRef").onclick=()=>{GL=null;GLTXT=null;renderMain()};
 $("#glFile").onclick=async()=>{const r=await api("/api/open_file",{path:GLSEL});if(r.result!=="ok")alert(GLSEL)};
 $("#glDir").onclick=async()=>{const d=GLSEL.replace(/\/[^\/]*$/,"");const r=await api("/api/open_path",{path:d});if(r.result!=="ok")alert(d)};
 $("#glCopy").onclick=async()=>{const txt=GLTXT?GLTXT.text:"";try{await navigator.clipboard.writeText(txt);$("#glCopy").textContent="✔ "+t("glCopied")}catch(e){prompt("",txt.slice(-20000))}}}
/* ---- diagnostics ---- */
let DG=null;
const dgT=v=>String(v).replace(/\bdg[A-Z]\w*\b/g,k=>t(k));
function diagView(){const mark={ok:'<span class="okline">✔</span>',warn:'<span class="warnline">⚠</span>',bad:'<span class="badline">✖</span>',info:'<span style="color:var(--mut)">•</span>'};
 const qk=DG&&DG[0]&&DG[0].quick||[];
 const quick=qk.length?`<div class="toolbar" style="margin-bottom:12px;flex-wrap:wrap">${qk.map(x=>`<button class="btn" style="padding:6px 10px" data-dgp="${esc(x.path)}" title="${esc(x.path)}">📂 ${esc(t(x.k))}</button>`).join("")}</div>`:"";
 const body=DG?quick+`<div class="grid2" style="grid-template-columns:repeat(auto-fill,minmax(420px,1fr))">${DG.filter(s=>!s.quick).map(s=>`<div class="box"><b>${esc(t(s.key))}</b>
  <table style="width:100%;margin-top:6px;font-size:13px"><tbody>${s.rows.map(r=>`<tr><td style="width:20px;vertical-align:top">${mark[r.st]}</td><td style="color:var(--mut);vertical-align:top;padding-right:10px;width:38%">${esc(t(r.k))}</td>
  <td class="mono" style="word-break:break-all;font-size:12px">${esc(dgT(r.v))}${r.path?` <a class="link" data-dgp="${esc(r.path)}" title="${esc(r.path)}">📂</a>`:""}</td></tr>`).join("")}</tbody></table></div>`).join("")}</div>`:`<div class="empty">${t("dgLoading")}</div>`;
 return pageWrap(`<h2>${t("diag")}</h2><p class="m">${t("dgIntro")}</p><div class="toolbar" style="margin-bottom:12px"><button class="btn pri" id="dgCopy">${t("dgCopy")}</button><button class="btn" id="dgRef">${t("dgRefresh")}</button></div>${body}`)}
function diagText(){const sym={ok:"OK  ",warn:"WARN",bad:"FAIL",info:"    "};return "Skyrim Mod Engine — "+t("diag")+" ("+new Date().toISOString()+")\n"+DG.filter(s=>!s.quick).map(s=>"\n== "+t(s.key)+" ==\n"+s.rows.map(r=>`[${sym[r.st]}] ${t(r.k)}: ${dgT(r.v)}`).join("\n")).join("\n")}
async function loadDiag(force){if(!DG||force){const r=await api("/api/diag");DG=r.sections||[];if(page==="diag")renderMain();return}
 document.querySelectorAll("[data-dgp]").forEach(a=>a.onclick=()=>api("/api/open_path",{path:a.dataset.dgp}));
 $("#dgRef").onclick=()=>{DG=null;renderMain()};
 $("#dgCopy").onclick=async()=>{const txt=diagText();try{await navigator.clipboard.writeText(txt);$("#dgCopy").textContent="✔ "+t("dgCopied")}catch(e){prompt(t("dgCopy"),txt)}}}
/* ---- adding archives: drag & drop anywhere, or a file picker ---- */
function toast(html){let d=$("#toast");if(!d){d=document.createElement("div");d.id="toast";d.className="toast";document.body.appendChild(d)}d.innerHTML=html;d.style.display=html?"block":"none"}
function upload(path,file,onp){return new Promise(res=>{const x=new XMLHttpRequest();x.open("POST",path+(path.includes("?")?"&":"?")+"name="+encodeURIComponent(file.name));x.setRequestHeader("X-Token",T);
 x.upload.onprogress=e=>onp&&onp(e.loaded);x.onload=()=>{try{res(JSON.parse(x.responseText))}catch(e){res({error:"HTTP "+x.status})}};x.onerror=()=>res({error:"network"});x.send(file)})}
async function addFiles(files){files=[...files];if(!files.length)return;
 const colls=files.filter(f=>/\.json$/i.test(f.name)),arcs=files.filter(f=>!/\.json$/i.test(f.name));
 if(colls.length){if(confirm(t("collDropQ").replace("%s",colls[0].name).replace("%p",S.profile))){const r=await upload("/api/upload_collection",colls[0]);if(r.error)alert(r.error==="busy"?t("busy"):t("collBadFile"));else{running=true;jobLine()}}}
 if(!arcs.length)return;const added=[],skipped=[];const total=arcs.reduce((a,f)=>a+f.size,0);let done=0;
 for(const f of arcs){const r=await upload("/api/upload",f,n=>toast(`⤒ ${esc(f.name)} — ${Math.floor((done+n)/Math.max(total,1)*100)}%`));done+=f.size;
  if(r.added)added.push(r.added);else skipped.push(f.name+" ("+t("skip_"+(r.skipped||"err"))+")")}
 toast("");if(skipped.length)alert(t("dropSkipped")+":\n"+skipped.join("\n"));
 if(added.length){const r=await api("/api/import_done",{added});if(r.error)alert(t("dropAddedBusy"));else{running=true;jobLine()}}await refresh()}
function pickFiles(accept,multi,cb){const i=document.createElement("input");i.type="file";i.accept=accept;i.multiple=multi;i.onchange=()=>cb(i.files);i.click()}
function pickArchives(){pickFiles(".zip,.7z,.rar,.tar,.gz,.xz,.bz2,.tgz",true,addFiles)}
function pickCollection(){pickFiles(".json",false,fs=>addFiles(fs))}
let dragDepth=0;
addEventListener("dragenter",e=>{if(![...(e.dataTransfer.types||[])].includes("Files"))return;e.preventDefault();dragDepth++;document.body.classList.add("dropping")});
addEventListener("dragover",e=>{if([...(e.dataTransfer.types||[])].includes("Files"))e.preventDefault()});
addEventListener("dragleave",e=>{if(--dragDepth<=0){dragDepth=0;document.body.classList.remove("dropping")}});
addEventListener("drop",e=>{e.preventDefault();dragDepth=0;document.body.classList.remove("dropping");if(e.dataTransfer.files.length)addFiles(e.dataTransfer.files)});
/* ---- FOMOD wizard: the engine asks, the page answers ---- */
let FQ=null;
async function fomodStart(mod,redo){const r=await api("/api/fomod_start",{mod,redo});if(r.error){alert(r.error==="busy"?t("busy"):r.error);return}running=true;jobLine();renderMain()}
function closeDlg(){const o=$("#ovl");if(o)o.remove()}
function dlg(cls,html){closeDlg();const o=document.createElement("div");o.className="ovl";o.id="ovl";o.innerHTML=`<div class="dlg ${cls}">${html}</div>`;document.body.appendChild(o);return o}
function fomodQuestion(q){if(!q){if(FQ){FQ=null;closeDlg()}return}if(FQ&&FQ.id===q.id)return;FQ=q;
 const multi=["SelectAny","SelectAtLeastOne","SelectAll"].includes(q.type),none=q.type==="SelectAtMostOne";
 const def=new Set(q.default||[]),req=new Set(q.required||[]);
 const row=(o,i)=>{const dis=!o.usable||req.has(i)||q.type==="SelectAll";const chk=def.has(i)||req.has(i)||q.type==="SelectAll";
  return `<label class="opt ${!o.usable?"dis":""}" data-o="${i}"><input type="${multi?"checkbox":"radio"}" name="fq" value="${i}" ${chk?"checked":""} ${dis?"disabled":""}>
  <div><div>${esc(o.name)}</div><div class="k">${esc(o.kind)}${o.conflict?" · "+esc(o.conflict):""}${(o.why||[]).length?" · "+esc(o.why.join("; ")):""}</div></div></label>`};
 const o=dlg("",`<div class="dh"><b>${esc(q.mod||"FOMOD")}</b><div class="m">${esc(q.module||"")}${q.step?" — "+esc(q.step):""}</div></div>
 <div class="db"><div class="opts"><div style="font-weight:600;margin:4px 10px 2px">${esc(q.group)}</div><div class="m" style="color:var(--mut);font-size:12px;margin:0 10px 8px">${esc(q.rule||q.type)}</div>
 ${q.options.map(row).join("")}${none?`<label class="opt" data-o="-1"><input type="radio" name="fq" value="-1" ${def.size?"":"checked"}><div>${t("fmNone")}</div></label>`:""}</div>
 <div class="desc" id="fqDesc"></div></div>
 <div class="df"><span class="err">${q.error?esc(q.error):""}</span><button class="btn" id="fqCancel">${t("fmCancel")}</button><button class="btn pri" id="fqNext">${t("fmNext")}</button></div>`);
 const show=i=>{const op=q.options[i];o.querySelectorAll(".opt").forEach(e=>e.classList.toggle("cur",e.dataset.o==String(i)));
  $("#fqDesc").innerHTML=op?(op.image?`<img src="/api/file?t=${encodeURIComponent(T)}&path=${encodeURIComponent(op.image)}" alt="">`:"")+esc(op.description||""):""};
 o.querySelectorAll(".opt").forEach(e=>{e.onmouseenter=()=>show(+e.dataset.o);e.onclick=()=>show(+e.dataset.o)});
 const first=[...def][0];show(first!==undefined?first:0);
 $("#fqCancel").onclick=async()=>{await api("/api/fomod_answer",{id:q.id,cancel:true});FQ=null;closeDlg()};
 $("#fqNext").onclick=async()=>{const picked=[...o.querySelectorAll("input[name=fq]:checked")].map(e=>+e.value).filter(i=>i>=0);
  await api("/api/fomod_answer",{id:q.id,picked});closeDlg()}}
/* ---- Nexus mod browser ---- */
const BR={q:"",sort:"endorsements",offset:0,res:null,loading:false,adult:false,err:"",kind:"mods"};
async function brSearch(off){BR.offset=off||0;BR.loading=true;BR.err="";renderMain();const kind=BR.kind;
 try{const r=await api(`/api/${kind==="coll"?"browse_collections":"browse"}?q=${encodeURIComponent(BR.q)}&sort=${BR.sort}&offset=${BR.offset}&adult=${BR.adult?1:0}`);if(kind!==BR.kind)return;if(r.error){BR.err=r.error;BR.res=null}else{BR.res=r;if(kind==="coll")r.source="graphql"}}catch(e){BR.err=String(e)}
 BR.loading=false;if(page==="browse"){renderMain();const pg=document.querySelector(".page");if(pg)pg.scrollTop=0}}
function fmtNum(n){return n==null?"—":Number(n).toLocaleString(L==="uk"?"uk-UA":"en-GB")}
function collCards(r,lg){return `<div class="bgrid">${r.collections.map(c=>`<div class="bcard">
  ${c.picture?`<img class="pic" loading="lazy" referrerpolicy="no-referrer" src="${esc(c.picture)}" alt="">`:`<div class="pic"></div>`}
  <div class="bb"><div class="bn">${esc(c.name)} ${c.attached?`<span class="badge">${t("brCollOn")}</span>`:""}${c.adult?` <span class="badge bad">18+</span>`:""}</div>
  <div class="bm">${esc(c.author||"")}${c.category?" · "+esc(c.category):""}${c.mods!=null?" · "+fmtNum(c.mods)+" "+t("brCollMods"):""}${c.size?" · "+fmtSize(c.size):""} · 👍 ${fmtNum(c.endorsements)} · ⬇ ${fmtNum(c.downloads)}</div>
  <div class="bs">${esc(c.summary)}</div>
  <div class="ba">${lg?`<button class="btn pri" data-cnew="${esc(c.slug)}" data-cname="${esc(c.name)}"${running?" disabled":""}>${t("brCollNew")}</button><button class="btn" data-ccur="${esc(c.slug)}" data-cname="${esc(c.name)}"${running?" disabled":""}>${t("brCollCur")}</button>`:""}<button class="btn" data-url="${esc(c.url)}">${t("brPage")}</button></div></div></div>`).join("")||`<div class="empty">${t("brNothing")}</div>`}</div>`}
function browseView(){const r=BR.res,lg=!!S.nexus,pr=!!(S.nexus&&S.nexus.premium),coll=BR.kind==="coll";
 const sorts=[["endorsements",t("brSortEnd")],["downloads",t("brSortDl")],["updated",t("brSortUpd")]].concat(coll?[]:[["new",t("brSortNew")]]);
 const tabs=`<div class="chips" style="margin-bottom:10px"><button class="${coll?"":"on"}" data-brk="mods">${t("brKindMods")}</button><button class="${coll?"on":""}" data-brk="coll">${t("brKindColl")}</button></div>`;
 let body;if(BR.loading)body=`<div class="empty">${t("brLoading")}</div>`;else if(BR.err)body=`<div class="banner">${esc(BR.err)}</div>${lg?"":`<p class="m" style="margin-top:10px">${t("brNeedLogin")}</p>`}`;
 else if(!r)body=`<div class="empty">${t("brHint")}</div>`;
 else if(coll)body=`<p class="m">${t("brFound")}: ${r.total==null?"?":fmtNum(r.total)}</p>${lg?`<p class="m" style="font-size:12.5px">${t(pr?"brCollPr":"brCollFree")}</p>`:`<p class="m">${t("brCollLogin")}</p>`}${brPager(r)}${collCards(r,lg)}${brPager(r)}`;
 else body=`<p class="m">${t("brFound")}: ${r.total==null?"?":fmtNum(r.total)}${r.hidden_adult?` · ${t("brHidden")}: ${r.hidden_adult}`:""}</p>${brPager(r)}<div class="bgrid">${r.mods.map(m=>`<div class="bcard">
  ${m.picture?`<img class="pic" loading="lazy" referrerpolicy="no-referrer" src="${esc(m.picture)}" alt="">`:`<div class="pic"></div>`}
  <div class="bb"><div class="bn">${esc(m.name)} ${m.installed?`<span class="badge">${t("brHave")}</span>`:""}</div>
  <div class="bm">${esc(m.author||"")}${m.version?" · v"+esc(m.version):""} · 👍 ${fmtNum(m.endorsements)} · ⬇ ${fmtNum(m.downloads)}</div>
  <div class="bs">${esc(m.summary)}</div>
  <div class="ba">${lg?`<button class="btn pri" data-files="${m.mod_id}" data-name="${esc(m.name)}">${t(pr?"brDownload":"brFiles")}</button>`:""}<button class="btn" data-url="${esc(m.url)}">${t("brPage")}</button></div></div></div>`).join("")}</div>
  ${brPager(r)}`;
 return pageWrap(`<h2>${t("browse")}</h2>${tabs}<p class="m">${coll?t("brCollIntro"):t(lg?(pr?"brIntroPr":"brIntroFree"):"brIntroAnon")}</p>
 <div class="toolbar" style="margin-bottom:12px;flex-wrap:wrap"><div class="search" style="flex:1;min-width:180px"><input id="brQ" placeholder="${t(coll?"brCollSearch":"brSearch")}" value="${esc(BR.q)}"></div>
 <select id="brSort">${sorts.map(([k,n])=>`<option value="${k}" ${BR.sort===k?"selected":""}>${esc(n)}</option>`).join("")}</select>
 <label style="display:flex;gap:6px;align-items:center;font-size:13px"><input type="checkbox" id="brAdult" ${BR.adult?"checked":""}>18+</label>
 <button class="btn pri" id="brGo">${t("brGo")}</button></div>${body}`)}
function brPages(page,pages){if(!pages){const o=[];for(let n=Math.max(1,page-2);n<=page;n++)o.push(n);return o}
 const keep=[...new Set([1,pages,...[-2,-1,0,1,2].map(d=>page+d).filter(n=>n>=1&&n<=pages)])].sort((a,b)=>a-b);const o=[];let prev=0;for(const n of keep){if(n-prev>1)o.push(null);o.push(n);prev=n}return o}
function brPager(r){const page=r.page||1,pages=r.pages,more=r.has_more&&r.source==="graphql";
 return `<div class="toolbar pager" style="margin:12px 0;justify-content:center;flex-wrap:wrap;gap:4px"><button class="btn" data-pg="${page-1}" ${page>1?"":"disabled"}>←</button>
 ${brPages(page,pages).map(n=>n===null?`<span style="color:var(--mut);padding:0 4px">…</span>`:`<button class="btn ${n===page?"pri":""}" data-pg="${n}">${n}</button>`).join("")}
 <button class="btn" data-pg="${page+1}" ${more?"":"disabled"}>→</button>${pages?`<span style="color:var(--mut);margin-left:8px">${t("brPageOf").replace("%p",page).replace("%n",fmtNum(pages))}</span>`:""}</div>`}
function bindBrowse(){const q=$("#brQ");if(!q)return;q.onkeydown=e=>{if(e.key==="Enter"){BR.q=q.value;brSearch(0)}};
 $("#brGo").onclick=()=>{BR.q=q.value;brSearch(0)};$("#brSort").onchange=e=>{BR.sort=e.target.value;BR.q=q.value;brSearch(0)};$("#brAdult").onchange=e=>{BR.adult=e.target.checked;if(BR.res)brSearch(0)};
 document.querySelectorAll("[data-pg]").forEach(x=>x.onclick=()=>{const n=+x.dataset.pg;if(n>=1)brSearch((n-1)*20)});
 document.querySelectorAll("[data-url]").forEach(b=>b.onclick=()=>api("/api/open_url",{url:b.dataset.url}));
 document.querySelectorAll("[data-files]").forEach(b=>b.onclick=()=>modFiles(+b.dataset.files,b.dataset.name));
 document.querySelectorAll("[data-brk]").forEach(b=>b.onclick=()=>{if(BR.kind===b.dataset.brk)return;BR.kind=b.dataset.brk;BR.res=null;BR.err="";if(BR.kind==="coll"&&BR.sort==="new")BR.sort="endorsements";brSearch(0)});
 document.querySelectorAll("[data-cnew]").forEach(b=>b.onclick=async()=>{const base=b.dataset.cname.replace(/[\\/:*?"<>|]/g,"").trim().slice(0,60)||"Collection";
  const n=prompt(t("brCollProfName"),base);if(!n||!n.trim())return;const r=await api("/api/profile_new_coll",{name:n.trim(),source:b.dataset.cnew});
  if(r.error){alert(r.error==="busy"?t("busy"):r.error);return}running=true;jobLine();BR.res=null;page="collection";render()});
 document.querySelectorAll("[data-ccur]").forEach(b=>b.onclick=async()=>{if(!confirm(t("brCollCurQ").replace("%s",b.dataset.cname).replace("%p",S.profile)))return;
  await run("collection_nexus",b.dataset.ccur);BR.res=null;page="collection";render()});
 if(!BR.res&&!BR.loading&&!BR.err)brSearch(0)}
async function modFiles(id,name){const o=dlg("files",`<div class="dh"><b>${esc(name)}</b><div class="m">${t("brFilesOf")}</div></div><div class="db"><div class="empty">${t("brLoading")}</div></div><div class="df"><button class="btn" id="fClose">${t("fmClose")}</button></div>`);
 $("#fClose").onclick=closeDlg;const r=await api("/api/mod_files?mod="+id);const db=o.querySelector(".db");
 if(r.error){db.innerHTML=`<div class="banner">${esc(r.error==="login"?t("brNeedLogin"):r.error)}</div>`;return}
 db.innerHTML=`${r.premium?"":`<p class="m" style="color:var(--mut)">${t("brFreeNote")}</p>`}<table class="ftab">${r.files.map(f=>`<tr><td><b>${esc(f.name)}</b> ${f.primary?`<span class="badge">${t("brMain")}</span>`:""}<div class="m" style="color:var(--mut);font-size:12px">${esc(f.category)} · v${esc(f.version||"?")} · ${fmtSize(f.size)}</div>
 ${f.description?`<div style="font-size:12px;margin-top:4px;color:#c4d0e3">${esc(f.description.replace(/<[^>]*>/g," ").replace(/\[[^\]]*\]/g,"").slice(0,300))}</div>`:""}</td>
 <td style="text-align:right;white-space:nowrap"><button class="btn pri" data-dl="${f.file_id}">${t(r.premium?"brDownload":"brViaSite")}</button></td></tr>`).join("")||`<tr><td class="empty">—</td></tr>`}</table>`;
 db.querySelectorAll("[data-dl]").forEach(b=>b.onclick=async()=>{const x=await api("/api/download",{mod:id,file:+b.dataset.dl});
  if(x.error){alert(x.error==="busy"?t("busy"):x.error);return}if(x.result==="job"){closeDlg();running=true;jobLine();BR.res=null}else if(x.result==="no-opener")prompt(t("brOpenManual"),x.url);else b.textContent="✔ "+t("brOpened")})}
function renderMain(){const m=$("#main");let left;
 if(page==="term"){m.innerHTML=`<div class="col" style="grid-column:1/-1">${termView()}</div>`;bindTerm();return}
 if(page==="home")left=`<div class="col">${homeView()}</div>`;else if(page==="mods")left=`<div class="col">${modsView()}</div>`;else left=`<div class="col">${{diag:diagView,browse:browseView,collection:collectionView,fomod:fomodView,plugins:pluginsView,glogs:glogsView,deps:depsView,conflicts:conflictsView,deploy:deployView,backups:backupsView,settings:settingsView}[page]()}</div>`;
 m.innerHTML=left+`<div class="col rightcol">${statusCard()}${page==="mods"?detail():""}</div>`;bindStatus();if(page==="mods")bindMods();if(page==="home")bindHome();if(page==="backups")bindBackups();if(page==="browse")bindBrowse();if(page==="diag")loadDiag();if(page==="glogs")bindGlogs()}
function render(){document.documentElement.lang=L;document.body.dataset.drop=t(S&&S.drop_extract?"dropHereX":"dropHere");document.querySelectorAll("[data-i]").forEach(e=>e.textContent=t(e.dataset.i));$("#ver").textContent="v"+S.version;$("#langSel").value=L;$("#quitBtn").title=t("quit");nav();renderMain();jobLine()}
function jobLine(){const j=S&&S.job;$("#dot").className="dot"+(running?" run":"");const jt=$("#jobtxt"),ja=j?j.args.join(" "):"";jt.title=running?ja:"";jt.textContent=running?t("working")+" "+(ja.length>90?ja.slice(0,90)+"…":ja):(j&&j.rc?"rc="+j.rc:t("idle"))}
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
async function nxLogin(id){const k=$("#"+(id||"nxKey")).value.trim();if(!k)return;const r=await api("/api/nexus_login",{key:k});if(r.error)alert(t("nxLoginFail"));await refresh()}
async function nxLogout(){await api("/api/nexus_logout",{});await refresh()}
function nxDl(){const v=prompt(t("nxAsk"));if(v&&v.trim())run("nexus_download",v.trim())}
async function quit(){await api("/api/quit",{});document.body.innerHTML=`<div class="empty" style="padding:80px">${t("closed")}</div>`;setTimeout(()=>{try{window.close()}catch(e){}},300)}
$("#langSel").onchange=e=>run("lang",e.target.value);$("#quitBtn").onclick=quit;
(()=>{const go=()=>{page="term";render()};$("#logTog").textContent="⤢";$("#logTog").onclick=go;$("#log").onclick=go;document.querySelector(".log .lt").onclick=go})();
async function refresh(){if(page==="glogs"){GL=null;GLTXT=null}S=await api("/api/state");L=S.lang||"uk";running=!!(S.job&&S.job.running);if(sel&&!S.mods.find(m=>m.name===sel))sel=null;if(!sel&&S.mods.length)sel=S.mods[0].name;render()}
async function pollLog(){try{const r=await api("/api/log?from="+logFrom);const changed=r.lines.length||(r.partial||"")!==PART;
 if(r.lines.length){LOGTXT+=(LOGTXT?"\n":"")+r.lines.join("\n");if(LOGTXT.length>300000)LOGTXT=LOGTXT.slice(-200000)}PART=r.partial||"";
 for(const l of r.lines){if(l.startsWith("$ ")){NPROB=0;LASTPROB=""}else if(PROB_RE.test(l)){NPROB++;LASTPROB=l}}
 if(changed){const last=PART||r.lines.filter(l=>l.trim()).slice(-1)[0];const pre=$("#log");
  if(last!==undefined){pre.textContent=last.trim();pre.title=last;pre.classList.toggle("bad",PROB_RE.test(last))}termAdd(r.lines);probBadge()}
 if(!(r.job&&r.job.running)&&LASTPROB&&$("#log").title!==LASTPROB){const pre=$("#log");pre.textContent=LASTPROB.trim();pre.title=LASTPROB;pre.classList.add("bad")}
 logFrom=r.next;fomodQuestion(r.question);showNotice(r.notice);const was=running,wasTerm=termJob();running=!!(r.job&&r.job.running);if(S)S.job=r.job;
 if(was&&!running)await refresh();else{jobLine();if(page==="term"&&wasTerm!==termJob())renderMain()}}catch(e){}setTimeout(pollLog,running?300:1500)}
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

def gui_i18n() -> dict:
    """The I18N table of the web page, parsed from its JavaScript literal (the backend's hints use it too)."""
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
    parser = argparse.ArgumentParser(description=tr("Skyrim Mod Engine v0.11 — аналіз, розпакування, каталог модів, перевірка SKSE."))
    parser.add_argument("--archives", help=tr("Папка з архівами на цей запуск (за замовч. збережена через --set-archives, інакше ./mods-archives)"))
    parser.add_argument("--mods", help=tr("Папка з розпакованими модами на цей запуск (за замовч. збережена через --set-mods, інакше ./Mods)"))
    parser.add_argument("--set-archives", metavar="PATH", help=tr("Запам'ятати свою папку з архівами модів ('default' — назад до ./mods-archives)."))
    parser.add_argument("--set-game", metavar="PATH", help=tr("Запам'ятати папку гри, якщо її не знайдено автоматично ('default' — шукати самому)."))
    parser.add_argument("--set-skse-log", metavar="PATH", help=tr("Запам'ятати свій шлях до skse64.log ('default' — стандартний у префіксі Proton)."))
    parser.add_argument("--loot", choices=("on", "off"), help=tr("Сортувати плагіни за LOOT (on) чи лише за власними правилами (off); запам'ятовується."))
    parser.add_argument("--extract", metavar="FILE", action="append", help=tr("Розпакувати лише цей архів (можна кілька разів), звідки завгодно — без копії в папку архівів."))
    parser.add_argument("--collision", choices=("skip", "suffix"), default="skip", help=tr("З --yes: якщо папка мода з такою назвою вже є — пропустити архів (skip) або розпакувати в папку з суфіксом (suffix)."))
    parser.add_argument("--set-mods", metavar="PATH", help=tr("Запам'ятати свою папку для розпакованих модів ('default' — назад до ./Mods)."))
    parser.add_argument("--move", action="store_true", help=tr("З --set-archives/--set-mods: перенести вміст старої папки в нову."))
    parser.add_argument("--output", default="mod_engine_output", help=tr("Папка для звітів (за замовч. ./mod_engine_output)"))
    parser.add_argument("--scan-only", action="store_true", help=tr("Лише проаналізувати нові архіви, не розпаковувати."))
    parser.add_argument("--yes", action="store_true", help=tr("Розпаковувати нові архіви без питань."))
    parser.add_argument("--skse", action="store_true", help=tr("Перевірити SKSE, Address Library і SKSE-плагіни модів."))
    parser.add_argument("--game", help=tr("Папка гри (за замовч. шукається в бібліотеках Steam)."))
    parser.add_argument("--fomod", action="store_true", help=tr("Встановити FOMOD-моди (вибір варіантів) у папки «<мод> [FOMOD]»."))
    parser.add_argument("--fomod-redo", action="store_true", help=tr("Перевстановити вже встановлені FOMOD (з урахуванням збереженого вибору)."))
    parser.add_argument("--fomod-mod", metavar="NAME", help=tr("З --fomod/--fomod-redo: лише цей FOMOD-мод."))
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
    parser.add_argument("--remove-mod", metavar="NAME", help=tr("Видалити мод: папку, FOMOD-варіанти й архів (у кошик, якщо він є)."))
    parser.add_argument("--check-updates", nargs="?", const="auto", choices=("auto", "full"), help=tr("Перевірити на Nexus, чи вийшли нові версії встановлених модів (full — перечитати всі моди, а не лише оновлені)."))
    parser.add_argument("--update-mod", metavar="NAME", help=tr("Оновити мод до знайденої нової версії: завантажити (Premium) і замінити на місці — порядок, увімкнення й вибір FOMOD лишаються."))
    parser.add_argument("--update-all", action="store_true", help=tr("Оновити всі моди, для яких знайдено нові версії (завантаження — з Premium)."))
    parser.add_argument("--plugin-order", metavar="JSON|FILE|reset", help=tr("Власний порядок плагінів профілю (вище — раніше, нижче — перемагає): JSON-список, файл у форматі plugins.txt або reset. Якщо розгортання актуальне — одразу переписує plugins.txt."))
    parser.add_argument("--papyrus-log", choices=["on", "off"], help=tr("Увімкнути/вимкнути лог скриптів Papyrus у Skyrim.ini префікса (оригінал зберігається в backup)."))
    parser.add_argument("--game-logs", action="store_true", help=tr("Логи гри, SKSE, модів, краш-логи й Papyrus: список з кількістю помилок і підказками з останнього краш-логу."))
    parser.add_argument("--keep-archive", action="store_true", help=tr("З --remove-mod: архів лишити (і більше не розпаковувати)."))
    parser.add_argument("--profile-delete", metavar="NAME", help=tr("Видалити профіль (не активний і не розгорнутий)."))
    parser.add_argument("--profiles", action="store_true", help=tr("Показати список профілів."))
    parser.add_argument("--nexus-login", nargs="?", const="", metavar="KEY", help=tr("Увійти в Nexus Mods персональним API-ключем (без KEY — запитає)."))
    parser.add_argument("--nexus-logout", action="store_true", help=tr("Видалити збережений ключ Nexus."))
    parser.add_argument("--nexus-status", action="store_true", help=tr("Показати, чи виконано вхід у Nexus."))
    parser.add_argument("--nexus-download", metavar="MOD[:FILE]", help=tr("Завантажити файл мода з Nexus (лише Premium): ID мода, ID:ID_файлу або посилання на сторінку."))
    parser.add_argument("--nxm", metavar="URL", help=tr("Обробити посилання nxm:// («Mod Manager Download» на сайті)."))
    parser.add_argument("--nxm-handler", action="store_true", help=tr("Зареєструвати скрипт як обробник посилань nxm:// у системі."))
    parser.add_argument("--collection-nexus", metavar="LINK", help=tr("Взяти колекцію прямо з Nexus: посилання на сторінку колекції, nxm:// або її код."))
    parser.add_argument("--skse-ensure", action="store_true", help=tr("Якщо якийсь мод потребує SKSE, а його немає — завантажити (з Premium) або попередити."))
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
    parser.add_argument("--gui", nargs="?", const="auto", choices=("auto", "app", "gtk", "web"), help=tr("Графічний інтерфейс у вікні програми (WebKit, Chromium/Vivaldi або Firefox), інакше вкладкою в браузері (також: подвійний клік по скрипту). web — одразу вкладкою."))
    parser.add_argument("--lang", choices=LANGS, help="Interface language / мова інтерфейсу: en, uk (remembered / запам'ятовується).")
    args = parser.parse_args()
    if args.lang:
        save_lang(args.lang)
    # Double-click in a file manager: no arguments and no terminal -> graphical interface.
    if args.gui or (len(sys.argv) == 1 and not sys.stdin.isatty()):
        mode = args.gui or "auto"
        if mode == "gtk":  # the native GTK window was removed in 0.12: the app window replaces it
            print(tr("Окремого GTK-інтерфейсу більше немає — відкриваю інтерфейс у вікні програми."))
            mode = "app"
        return gui_run(sys.modules[__name__], Path(__file__).resolve(), args.output,
                       mode="tab" if mode == "web" else "app")

    if args.nxm:  # started by the browser: work in the script's own folder
        os.chdir(Path(__file__).resolve().parent)
    cwd = Path.cwd()
    output_root = (cwd / args.output).resolve()
    load_saved_paths(output_root)
    if args.set_game or args.set_skse_log or args.loot:
        rc = 0
        if args.set_game:
            if args.set_game.strip().lower() == "default":
                update_settings(output_root, game_dir=None)
                SAVED_PATHS["game_dir"] = None
                gd = find_game_dir(None)
                print(tr("Папку гри шукаю автоматично: {0}", gd if gd else tr("не знайдено")))
            else:
                gp = Path(args.set_game).expanduser().resolve()
                if not is_game_dir(gp):
                    print(tr("У «{0}» немає SkyrimSE.exe — це не папка гри. Нічого не змінено.", gp))
                    rc = 1
                else:
                    update_settings(output_root, game_dir=str(gp))
                    SAVED_PATHS["game_dir"] = str(gp)
                    print(tr("Папка гри: {0}", gp))
        if args.set_skse_log:
            if args.set_skse_log.strip().lower() == "default":
                update_settings(output_root, skse_log=None)
                SAVED_PATHS["skse_log"] = None
                print(tr("skse64.log: стандартний шлях у префіксі Proton."))
            else:
                lp = Path(args.set_skse_log).expanduser().resolve()
                if lp.is_dir():
                    lp = lp / "skse64.log"
                update_settings(output_root, skse_log=str(lp))
                SAVED_PATHS["skse_log"] = str(lp)
                print(tr("skse64.log: {0}", lp) + ("" if lp.is_file() else tr(" (файлу поки немає — з'явиться після запуску гри через SKSE)")))
        if args.loot:
            update_settings(output_root, loot=args.loot == "on")
            print(tr("Сортування плагінів за LOOT: {0}", tr("увімкнено") if args.loot == "on" else tr("вимкнено")))
        if not (args.yes or args.scan_only or args.fomod or args.fomod_redo or args.deploy or args.conflicts or args.skse
                or args.set_archives or args.set_mods):
            return rc
    if read_settings(output_root).get("loot") is False:
        args.no_loot = True
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
    remove_rc = 0
    if args.remove_mod:
        remove_rc = remove_mod(output_root, mods_root, archives_root, args.remove_mod, args.keep_archive)
        if not (args.yes or args.scan_only or args.fomod or args.fomod_redo or args.deploy):
            return remove_rc
    pdir = profile_dir(output_root, profile)
    modlist_path = pdir / "modlist.txt"
    if args.plugin_order is not None:
        try:
            uo = parse_plugin_order_arg(args.plugin_order)
        except (OSError, ValueError) as ex:
            print(tr("Порядок плагінів не прочитано: {0}", ex))
            return 1
        pdir.mkdir(parents=True, exist_ok=True)
        if uo and uo["order"]:
            (pdir / USER_PLUGIN_ORDER).write_text(json.dumps(uo, ensure_ascii=False, indent=1), encoding="utf-8")
            print(tr("Власний порядок плагінів профілю «{0}» збережено: {1} плагін(ів), вимкнено {2}.",
                     profile, len(uo["order"]), len(uo["disabled"])))
        else:
            (pdir / USER_PLUGIN_ORDER).unlink(missing_ok=True)
            print(tr("Власний порядок плагінів профілю «{0}» скинуто — знову діють LOOT і правила рушія.", profile))

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
    if args.skse_ensure:
        gd = find_game_dir(args.game)
        if gd:
            skse_ensure(gd, mods_root, archives_root, output_root)
        else:
            print(tr('\nПапку гри не знайдено. Вкажи її через --game "/шлях/до/Skyrim Special Edition"'))
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
    if args.check_updates:
        acc = nexus_account()
        if not acc:
            print(tr("Спершу увійди в Nexus: --nexus-login"))
            return 1
        try:
            check_updates(output_root, archives_root, acc["api_key"], full=args.check_updates == "full")
        except NexusError as ex:
            print(tr("ПОМИЛКА: {0}", ex))
            return 1
        if not (args.update_all or args.update_mod or args.yes or args.deploy):
            return 0
    if args.update_mod or args.update_all:
        acc = nexus_account()
        res = load_updates(output_root).get("results") or {}
        if args.update_all:
            todo = [r for r in res.values() if r.get("status") in ("update", "downloaded")]
        else:
            fm = FOMOD_OUT_RE.match(args.update_mod)
            base = fm.group(1) if fm and (mods_root / fm.group(1)).is_dir() else args.update_mod
            todo = [r for r in res.values() if r.get("folder") == base and r.get("status") in ("update", "downloaded")]
            if not todo:
                print(tr("Для «{0}» нової версії не знайдено — спершу --check-updates", base))
                return 1
        print(tr("\n=== ОНОВЛЕННЯ МОДІВ: {0} ===", len(todo)))
        failed = 0
        for r in todo:
            if r["status"] == "downloaded":
                print(tr("{0}: новий архів уже є — розпакую", r["folder"]))
                continue
            if not acc:
                print(tr("Спершу увійди в Nexus: --nexus-login"))
                return 1
            if not acc.get("is_premium"):
                print(tr("{0}: без Premium завантаж файл на сайті («Mod Manager Download»), потім «Розпакувати нові»:\n  {1}",
                         r["folder"], nexus_mm_url(int(r["mod"]), int(r["target"]))))
                failed += 1
                continue
            print(f"{r['folder']}: {r.get('cur_version') or '?'} → {r.get('version') or '?'}", flush=True)
            try:
                nexus_download(int(r["mod"]), int(r["target"]), archives_root, acc["api_key"])
            except NexusError as ex:
                print(tr("  ПОМИЛКА: {0}", ex))
                failed += 1
        nexus_rc = 1 if failed else nexus_rc
        args.yes = True  # unpack: the new archives replace the old versions in place
        args.scan_only = False
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
    if args.extract:  # given archives only, wherever they are (drag & drop "unpack right away")
        all_archives = []
        for x in args.extract:
            ap = Path(x).expanduser().resolve()
            if ap.is_file() and is_archive(ap):
                all_archives.append(ap)
            else:
                print(tr("Не архів або не знайдено: {0}", ap))
    else:
        all_archives = sorted(p for p in archives_root.rglob("*") if p.is_file() and is_archive(p))
    archive_paths = [p for p in all_archives if not SECONDARY_VOLUME_RE.search(p.name)]
    report = {
        "engine_version": "0.11",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "archives_directory": str(archives_root),
        "mods_directory": str(mods_root),
        "archive_tools": sorted(tools),
        "archives": [],
        "mods": [],
    }

    print(f"Skyrim Mod Engine v{APP_VERSION}")
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
    updated_mods: set[str] = set()  # replaced in place by a newer version of the same Nexus file
    for archive in archive_paths:
        # Outside the archives folder the full path is the key (an archive unpacked straight from Downloads).
        rel = str(archive.relative_to(archives_root)) if archive.is_relative_to(archives_root) else str(archive)
        st = archive.stat()
        prev = state.get(rel, {})
        if prev.get("removed") and prev.get("size") == st.st_size and prev.get("mtime_ns") == st.st_mtime_ns:
            report["archives"].append({"archive": rel, "size": human_size(st.st_size), "status": "removed"})
            skipped_known += 1
            continue  # the player removed this mod and kept the archive
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
        update_of = None
        if destination.exists():
            update_of = archive_update_of(output_root, state, archive, destination)
        if update_of:
            print(tr("  Нова версія мода «{0}» (було: {1}) — замінюю на місці, порядок і вибір лишаються", destination.name, Path(update_of).name))
        elif destination.exists():
            print(tr("  Папка вже існує: {0}", destination.name))
            if args.yes and args.collision != "suffix":
                print(tr("  --yes задано, але наявну папку не перезаписую; архів пропущено.\n"))
                rec["status"] = "skipped-name-collision"
                report["archives"].append(rec)
                continue
            answer = "o" if args.yes else ask(tr("  [o] розпакувати в нову папку з суфіксом / [s] пропустити? [s]: "))
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
        if update_of and not args.yes:
            answer = ask(tr("  Замінити «{0}» новою версією? [Y/n]: ", destination.name))
            if answer not in ("", "y", "yes", "т", "так"):
                print(tr("  Пропущено.\n"))
                rec["status"] = "skipped"
                report["archives"].append(rec)
                continue
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
            if update_of:
                finish_mod_update(output_root, mods_root, archives_root, state, update_of, destination)
                updated_mods.add(destination.name)
            staging.rename(destination)
            if not update_of:
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
    if updated_mods:  # an updated FOMOD mod: reinstall its variant in place with the saved choices
        folders_now = {p.name for p in mods_root.iterdir() if p.is_dir()}
        owned_now = fomod_owned(pdir, {m["name"] for m in report["mods"] if m["layout"] == "fomod"}, folders_now)
        for name in sorted(updated_mods):
            variant = owned_now.get(name)
            if not variant:
                continue
            print(tr("\nОновлений FOMOD-мод «{0}»: перевстановлюю «{1}» зі збереженим вибором", name, variant))
            install_fomods(mods_root, output_root, report["mods"], find_game_dir(args.game), False, True,
                           collection_fomod_choices(coll, idx_to_mod) if coll else None, profile, pdir, name)
            if (mods_root / variant).is_dir():
                sv = summarize_mod(mods_root / variant, index_dir)
                live_index.add(sv["index"])
                report["mods"] = [m for m in report["mods"] if m["name"] != variant] + [sv]
            mark_redeploy(output_root, variant)
            others = [pn for pn in list_profiles(output_root) if pn != profile
                      and fomod_owned(profile_dir(output_root, pn), {name}, folders_now).get(name)]
            if others:
                print(tr("  У профілях {0} цей мод теж встановлено — перевстанови його там («Пакети» → «Вибрати варіанти» або --fomod-redo).", ", ".join(others)))
    if args.fomod or args.fomod_redo:
        interactive = (sys.stdin.isatty() or FOMOD_IO or os.environ.get("SME_TERM") == "1") and not args.auto and not args.yes
        new_dirs, renames = install_fomods(mods_root, output_root, report["mods"], find_game_dir(args.game),
                                           interactive, args.fomod_redo,
                                           collection_fomod_choices(coll, idx_to_mod) if coll else None,
                                           profile, pdir, args.fomod_mod)
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
    elif args.plugin_order is not None:
        game = find_game_dir(args.game)
        if not (game and reapply_plugin_order(game, mods_root, output_root, on_mods, on_order,
                                              not args.no_loot, coll, profile, pdir)):
            print(tr("Новий порядок застосується при наступному розгортанні (--deploy)."))

    if args.papyrus_log:
        game = find_game_dir(args.game)
        if not game:
            print(tr('\nПапку гри не знайдено. Вкажи її через --game "/шлях/до/Skyrim Special Edition"'))
        else:
            set_papyrus_logging(game, output_root, args.papyrus_log == "on")

    if args.game_logs:
        game = find_game_dir(args.game)
        if not game:
            print(tr('\nПапку гри не знайдено. Вкажи її через --game "/шлях/до/Skyrim Special Edition"'))
        else:
            print_game_logs(game)

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
    elif not args.undeploy and not (args.deploy and not args.dry_run) and args.plugin_order is None:
        if os.environ.get("SME_GUI") != "1":  # in the GUI deployment is a button of its own
            print(tr("\nПримітка: без --deploy скрипт не змінює папку гри і plugins.txt."))
    return 0 if deploy_ok else 1

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print(tr("\nЗупинено користувачем."))
        raise SystemExit(130)
