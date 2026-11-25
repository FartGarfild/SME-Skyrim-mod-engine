# Skyrim Mod Engine — test version 0.11

A single-file mod manager for **Skyrim Special Edition / Anniversary Edition on Steam Deck and Linux**.
It runs natively (plain Python, no Wine for the manager itself) and prepares a Proton game install:
extracts archives, installs FOMODs, orders mods and plugins, detects conflicts, deploys into the game
folder with a full rollback, and checks that SKSE and its plugins will actually load.

> **Test version.** It has been tested end to end on one Steam Deck with one large Vortex collection
> (566 mods, 443 plugins, SKSE + 72 SKSE plugins loading). Please read *Known limitations* and keep
> a backup of your game folder.

> **Interface language:** English or Ukrainian. By default it follows the system locale (`uk_*` → Ukrainian,
> anything else → English). Switch with `--lang en` or `--lang uk`; the choice is remembered in
> `mod_engine_output/settings.json`. The `SME_LANG` environment variable overrides it for one run.

---

## What it does

| Area | What the script does |
|---|---|
| Archives | `.zip`, `.7z`, `.rar` (incl. multi-volume and solid), `.tar*`. Picks the best tool per format (`unrar` for RAR, `7z` for the rest, Python for ZIP with a `7z` fallback for Deflate64 etc.). Rejects archives with symlinks; a failed extraction leaves nothing behind. |
| Mod layout | Finds the real `Data` folder inside each mod: wrapper folders, `Data/`, SKSE packages (`skse64_x_xx_xx/` + `Data/`), MO2 `Root/`, ENB/root-only mods, loose SKSE plugins, Data-root INI configs (SPID, KID, BOS, MTD…), case differences (`DATA`, `Textures`). |
| FOMOD | Full installer: steps, groups, flags, conditional installs, priorities, visibility. Interactive (wizard in the GUI, number keys in the terminal) or `--auto`. Auto mode checks plugin masters (reads ESP headers), SKSE presence, requirements stated in descriptions ("REQUIRES …"), and picks the best option across "-OR-" groups. |
| Vortex collections | Reads `collection.json`: matches your archives by MD5, installs FOMODs **with the collection author's choices**, applies the author's mod order rules and plugin load order, lists missing archives with Nexus links. |
| Mod order | `mod_engine_output/profiles/PROFILE/modlist.txt` — one mod per line, **lower line wins** (like MO2); `-Mod name` disables a mod. New mods are placed after the mods they depend on. Manual edits are respected; dependency violations are reported. |
| Profiles | Several setups over one shared `Mods/` folder: own mod order, enabled mods, FOMOD choices and an `overwrite/` folder for files the game creates or changes (MCM settings, plugin configs, tool output). Switching = full rollback of the old profile, then deployment of the new one. |
| Conflicts | `--conflicts`: who overrides whom, case-insensitive, identical files ignored, BSA contents included (loose files always beat BSAs). Severity: plugins/BSA → critical, scripts/DLL/UI/animations → important, configs → moderate, textures/meshes/sounds → cosmetic. |
| Plugin order | LOOT masterlist rules ("after", groups, dirty/incompatible/missing warnings), downloaded from GitHub and cached; with a collection, the author's order comes first; your own order from the *Plugin order* page wins over both. Falls back to master-based ordering when offline without a cache. Checks the 254 full / 4096 light plugin limits. |
| Deployment | Hard links into the game folder (no extra disk space); configs the game may rewrite (`.ini`, and `.json/.toml/.yaml` under `SKSE/Plugins` and `MCM`) are copied instead, so changes stay in the profile. Every overwritten game file is backed up first. A journal is written **before** the first change, so `--undeploy` restores the game byte for byte even after a crash, sleep or `kill -9` mid-deployment. Files changed by the game afterwards are set aside, never deleted. |
| SKSE / Proton | `--skse`: game version, SKSE build, Address Library, a static compatibility check of every SKSE plugin DLL, and a parsed `skse64.log` from the Proton prefix. Checks Visual C++ runtime in the prefix and Steam launch options. Supports Skyrim Runtime Swapper (SRS). |

---

## Requirements

- Steam version of Skyrim SE/AE, launched at least once through Steam (creates the Proton prefix).
- `python3` (tested with Python 3.13 and the `python3` that ships with SteamOS; no extra packages).
- Archive tools: `7z`, `bsdtar` and `unrar` are present on SteamOS. Without them only ZIP works.
- For most SKSE plugins under Proton: the real Microsoft Visual C++ runtime in the game prefix (see below).

## Installation

1. Put `mod_engine.py` into your game folder, e.g.
   `~/.local/share/Steam/steamapps/common/Skyrim Special Edition/`.
2. Create `mods-archives/` next to it and copy your mod archives there (e.g. from Vortex's
   `downloads/skyrimse/` folder).
3. Open Konsole in that folder. All commands below are run from there.

**Own folders.** By default archives are read from `mods-archives/` and mods are extracted to `Mods/`
next to the script. Any other folder can be set once and is remembered (also in the GUI, *Settings*):

```bash
python3 mod_engine.py --set-archives ~/Downloads/skyrim-mods          # e.g. where the browser saves them
python3 mod_engine.py --set-mods "/run/media/deck/SD/SkyrimMods" --move   # --move takes the old contents along
python3 mod_engine.py --set-mods default                              # back to ./Mods
```

Archives can live anywhere. Extracted mods should stay on the **same drive as the game**: otherwise
deployment cannot use hard links and copies the files (the script warns and checks free space). Moving
the mods folder is refused while mods are deployed.

The script creates `Mods/` (extracted mods) and `mod_engine_output/` (reports, journal, backups).
Without `--deploy` it never touches the game folder or `plugins.txt`.

---

## Graphical interface

The interface is built into `mod_engine.py` (nothing extra to download). Make the script executable
(Dolphin: right click → Properties → Permissions → *Is executable*, or `chmod +x mod_engine.py`) and
double-click it — or run `python3 mod_engine.py --gui`.

- **One interface, shown as an app window.** The program opens its interface in the first thing available:
  a WebKit view in its own window (when WebKit2GTK is installed), a Chromium-type browser in app mode —
  a window without tabs or address bar (Vivaldi, Chrome, Chromium, Brave, Edge; native or Flatpak) —
  Firefox with a separate profile whose toolbars are hidden (your own profile is not touched), or a normal
  browser tab. (The separate native GTK window of earlier versions has been removed; `--gui gtk` now opens
  the app window.)
- Force a variant: `--gui app` (default) or `--gui web` (a browser tab). The page's server listens on 127.0.0.1 only, with a
  secret token, and stops about a minute after the window is closed.

Mods with on/off switches and drag-and-drop order, profiles, FOMOD choices, the collection, conflicts,
deployment and SKSE checks, backups and game protection, Nexus login, folders and language — plus a *Play*
button that deploys first when needed. Every action runs the same commands as the console, shown live in
the event log.

- **Home page** — the setup at a glance: profile and collection, enabled mods and size, game folder and
  version, SKSE, last deployment, game protection, what deserves a look (dependency problems, conflicts,
  files in overwrite) — and the Nexus login right there. The mod list itself is on the *Mods* page.
- **All automatic** — one button (home page and *Mods*): unpacks new archives; when a mod needs SKSE and
  it is missing, downloads it (Premium key) or warns; installs FOMODs with automatic choices and deploys the
  profile. It does not start the game — it ends with "Done — ready to play" and a list of anything worth a look.
- **Removing a mod** — *Remove mod* (mod details or the ⋮ menu) removes its folder, its FOMOD variants, its
  place in every profile and, if ticked, its archive. Everything goes to the system trash when there is one.
  An archive that is kept is not unpacked again.
- **Mod browser** — search Nexus Mods from the program (name, sort by endorsements / downloads / updates /
  new, 18+ hidden unless ticked), pictures, the mod's files list. With **Premium** a file is downloaded and
  unpacked right there. Without Premium Nexus allows downloads only from its website: *Via site* opens the
  exact file page, and its *Mod Manager Download* button hands the file to this program (turn on the nxm
  handler in Settings once). Searching works without logging in; the file list needs the API key.
  The *Collections* tab searches Nexus collections for this game (name, author, number of mods, size,
  endorsements). *Into a new profile* creates a profile from the collection (your current one stays as it
  is), *Into the current profile* attaches it to the active one; then the Collection page opens with the
  archives to get — with Premium the program downloads them, without it each one goes through
  *Mod Manager Download* on the site.
- **Mod updates** — *Check for updates* on the Mods page asks Nexus about every installed mod whose
  archive came from Nexus (recognised by the Nexus file name, or remembered when the program downloaded
  it). The new file is the one the author marked as replacing yours on Nexus; when an old file was only
  archived without a replacement, a newer file with the same name is offered and marked *check it*. The first
  check (and one a month) reads every mod's file list — for a big setup a few hundred requests, a few minutes;
  in between only the mods Nexus reports as updated are re-read. Updatable mods get a *→ new version*
  badge and a filter. *Update* (Premium) downloads the file and replaces the mod in place: the same folder
  name, so order, enabled state and profiles stay; a FOMOD mod is reinstalled with the saved choices; the
  old folder and archive go to the trash. Without Premium *Download on the site* opens the file page; after
  *Mod Manager Download*, *Unpack new* does the replacement. A second archive of the same mod with a
  similar name (a patch, an optional file) never replaces the main one: only the file Nexus named as the
  update does. Then deploy again (the banner says so).
- **Adding mods** — drag archives from the file manager (or the browser's downloads) into the window, or
  *Add mods → Choose archives*. They are copied into the archives folder (the original stays where it was)
  and unpacked right away. With *Unpack right away, no copy in mods-archives* ticked (next to the hint
  on the mods page) a dropped archive is unpacked straight from where it lies and nothing is copied; the
  browser page cannot see file paths, so there the file is uploaded to a temporary folder and deleted
  after unpacking. Such archives are not in `mods-archives/`: a collection's MD5 check does not see them,
  and a deleted mod folder is not unpacked again on the next scan. A dropped
  `collection.json` is offered as the profile's collection.
- **Everything else from the console** — game folder and `skse64.log` path (*Settings*), LOOT on/off,
  SKSE via Steam's *Play* (*Deployment*), attach/detach a `collection.json`, paste an `nxm://` link,
  unpack archives whose folder name is taken into `NAME (2)`. Hints in the event log name the page and
  button instead of console options.
- **Log and terminal** — the full event log has its own page. Every line also goes to a file in RAM
  (`/run/user/…` or `/dev/shm/skyrim-mod-engine-…/session.log`, no disk writes), started clean on every
  launch and limited to 50 MB (the newest lines are kept), so long jobs are not cut in the window; *Open the log file* / *Save a copy* (to `mod_engine_output/logs/`). Errors are red,
  warnings yellow, with an *errors and warnings only* filter; the bottom bar wraps long lines, keeps the
  last error visible after a job and shows the number of problems.
  Below it: an SME console that takes the same options as the command line (`--deploy --dry-run`, `--fomod-redo` …) and answers the
  program's questions in place (not a system shell). *Open Konsole* opens the desktop terminal in the SME
  folder, where `sme` = `python3 mod_engine.py`.
- **Diagnostics page** — paths and versions in one place: game folder and `SkyrimSE.exe` version, Proton
  prefix, `Plugins.txt`, saves, SKSE (loader, build for this game version, Address Library, `skse64.log`),
  the Play-button launcher, LOOT masterlist, Nexus login and nxm handler, mods/archives folders (and whether
  they are on the game's drive, i.e. hard links or copies), backups and tools. Every row is marked
  OK / warning / problem, folders open with one click, and *Copy report* gives a text for bug reports
  (no API key in it).
- **Game and mod logs** — every log in one list, newest first: `skse64.log` and the logs of SKSE plugins
  (`Documents/My Games/…/SKSE` in the Proton prefix), crash logs (Crash Logger, Trainwreck), Papyrus
  (`Logs/Script`), logs in `Data/SKSE/Plugins`, the game folder and Proton (`~/steam-489830.log` with
  `PROTON_LOG=1`). Each shows its error/warning count; a log more than an hour older than `skse64.log` is
  marked *old* (an earlier session or a removed mod). Viewer: errors red, warnings yellow, *errors and warnings
  only*, search, open file/folder, copy. The latest crash log is summarised on top: the exception line and the
  mod DLLs/plugins it mentions most — a hint, not proof of the culprit. A new crash log from the last session
  puts *!* on the menu item. *Turn on the Papyrus log* sets `[Papyrus] bEnableLogging/bEnableTrace/
  bLoadDebugInformation` in the prefix's `Skyrim.ini` (original kept in `mod_engine_output/backup/`). Logs are
  only read, never changed. Error counting is by keywords and level tags (`[error]`, `[warning]`), so a line can
  be marked wrongly now and then.
- **Plugin order** — the deployed load order with ↑ ↓ and *to top / to bottom* buttons: lower in the list loads
  later and wins when two plugins edit the same record (NPC, item, leveled list). The rules the game enforces
  anyway are kept: masters (ESM, ESL, ESP with the ESM flag) stay above regular plugins, and a plugin cannot
  move above its own masters (the page says why when a move is limited). The check box turns a plugin off;
  plugins that need it go off with it, and turning one on also turns on its masters. *Save and apply* stores
  the order in the profile (`profiles/NAME/plugin_order.json`) and, if the deployment is up to date, rewrites
  only `plugins.txt` — no redeployment. A saved order wins over LOOT and the collection; plugins of mods added
  later are placed by LOOT next to their neighbours. *Back to automatic order* forgets it.
- **FOMOD wizard** — *Choose options* on the FOMOD page shows the installer step by step: radio buttons or
  check boxes as the group requires, option descriptions and pictures, required/unavailable options locked.
  *Cancel* leaves everything as it was; a new install replaces the old folder only when it is complete.

## Quick start

```bash
python3 mod_engine.py --yes                          # extract new archives, build the catalog
python3 mod_engine.py --fomod --auto                 # install FOMOD mods (or without --auto to choose)
python3 mod_engine.py --conflicts                    # review conflicts and modlist.txt
python3 mod_engine.py --deploy --dry-run             # see the plan; nothing is changed
python3 mod_engine.py --deploy                       # deploy into the game
# play, then:
python3 mod_engine.py --skse                         # what actually loaded
python3 mod_engine.py --undeploy                     # full rollback, any time
```

### With a Vortex collection

```bash
python3 mod_engine.py --yes --collection ./collection.json   # remembered for later runs
python3 mod_engine.py --fomod --auto                          # author's FOMOD choices
python3 mod_engine.py --deploy --dry-run
python3 mod_engine.py --deploy
```

`--collection none` stops using the collection.

### Profiles

```bash
python3 mod_engine.py --profiles                       # list (active / deployed)
python3 mod_engine.py --profile-new "Light"            # new profile, all mods disabled, becomes active
python3 mod_engine.py --profile-new "Coll" --collection ./collection.json      # only the collection's mods enabled
python3 mod_engine.py --profile-new "Coll2" --collection-nexus "COLLECTION-LINK"
# enable mods: remove the "-" in mod_engine_output/profiles/Light/modlist.txt (or switch them on in the GUI)
python3 mod_engine.py --fomod-redo                     # different FOMOD choices for this profile only
python3 mod_engine.py --deploy                         # removes the deployed profile, deploys "Light"
python3 mod_engine.py --profile Default --deploy       # back
python3 mod_engine.py --profile-delete "Light"
```

- A new profile takes only the mod order from the current one; every mod starts **disabled** so you pick
  what this profile uses. Created with a collection, exactly the collection's mods are enabled (then
  `--fomod-redo --auto` applies the author's FOMOD choices for that profile).
- Each profile has its own collection (`profiles/NAME/collection.json`). Mods extracted while a profile is
  active are enabled there; other profiles see them disabled.
- The first run of 0.10 turns the existing `modlist.txt`, FOMOD choices and collection into the profile **Default**.
- A FOMOD reinstalled with other choices gets its own folder (`MOD [FOMOD 2]`) when another profile
  still uses the first one; profiles with identical choices share a folder. Hard links keep the extra
  folders almost free.
- While a profile is deployed, files the game or a tool **creates** in `Data` or **changes** in a
  copied config are moved into `profiles/NAME/overwrite/` on rollback/switch and deployed again
  above all mods next time (like MO2's overwrite).
- Saves are shared between profiles for now.

---

## All options

| Option | Purpose |
|---|---|
| `--yes` | Extract new archives without asking. |
| `--scan-only` | Only analyze new archives. |
| `--fomod` / `--fomod-redo` | Install FOMODs / reinstall them (saved choices are reused). |
| `--auto` | No questions in FOMOD installers. |
| `--fomod-mod NAME` | With `--fomod` / `--fomod-redo`: only this FOMOD mod. |
| `--collection FILE` | Use a Vortex `collection.json` (`none` to disable). |
| `--conflicts` | File conflicts between mods. |
| `--deploy` / `--dry-run` | Deploy mods into the game / only show the plan. |
| `--undeploy` | Remove deployed files, restore originals and `plugins.txt`. |
| `--skse` | SKSE, Address Library, plugin and Proton checks. |
| `--skse-launcher` / `--skse-launcher-restore` | Make Steam's *Play* start SKSE by replacing `SkyrimSELauncher.exe` (with backup). Not used with SRS — see below. |
| `--no-loot` / `--loot-update` | Disable LOOT rules for this run / refresh the masterlist now (otherwise weekly). |
| `--game-logs` | List game, SKSE, mod, crash and Papyrus logs with error counts and hints from the latest crash log. |
| `--papyrus-log on` / `off` | Turn the Papyrus script log on/off in the prefix's `Skyrim.ini` (original backed up). |
| `--check-updates` [`full`] | Check Nexus for new versions of installed mods (`full` re-reads every mod). |
| `--update-mod NAME` / `--update-all` | Update to the new version found: download (Premium) and replace in place — order, enabled state and FOMOD choices stay. |
| `--loot on` / `--loot off` | Remember whether plugins are sorted with LOOT. |
| `--plugin-order JSON`, `FILE` or `reset` | The profile's own plugin order: a JSON list (or `{"order": [...], "disabled": [...]}`) or a file in `plugins.txt` format (`*` = active). Rewrites `plugins.txt` right away when the deployment is up to date. |
| `--game PATH` / `--set-game PATH` | Game folder, if not found in Steam libraries (`--set-game` remembers it, `default` resets). |
| `--skse-log PATH` / `--set-skse-log PATH` | Custom `skse64.log` location (for one run / remembered). |
| `--skse-ensure` | If a mod needs SKSE and it is missing: download it (Premium) or warn. |
| `--remove-mod NAME` [`--keep-archive`] | Remove a mod: folder, FOMOD variants, profile entries and archive (to the trash); `--keep-archive` keeps the archive and stops it being unpacked again. |
| `--extract FILE` | Unpack only this archive (repeatable), from any folder, without copying it to the archives folder. |
| `--collision skip` / `--collision suffix` | With `--yes`: when a mod folder with that name exists, skip the archive (default) or unpack into `NAME (2)`. |
| `--set-archives PATH` / `--set-mods PATH` [`--move`] | Remember your own archives / mods folder (`default` resets). |
| `--archives`, `--mods`, `--output` | Folders for one run only. |
| `--gui`, `--gui web` | Open the graphical interface in an app window, or as a browser tab with `web` (also: double-click without a terminal). |
| `--backup-game [NAME]` [`--backup-mode full` or `link`] [`--with-saves`] | Back up the clean game. |
| `--restore-game NAME` / `--backups` / `--backup-verify NAME` / `--backup-delete NAME` | Restore / list / verify / delete game backups. |
| `--incremental-backup on` / `off` | Keep originals of game files that mods override (off until you enable it). |
| `--game-check` / `--game-repair` / `--game-baseline` | Compare the game with its clean file list / clean it up / record the list again. |
| `--nexus-login [oauth/KEY]` / `--nexus-logout` / `--nexus-status` | Nexus account: sign in through the browser (OAuth) or, for testing, a personal API key. |
| `--nexus-download MOD[:FILE]` | Download from Nexus (Premium; ID, ID:file ID or page link). |
| `--nxm-handler` / `--nxm URL` | Register as `nxm://` handler / handle one link. |
| `--collection-nexus LINK` | Get a collection from Nexus (page link, nxm:// or code). |
| `--collection-download` [`--with-optional`] | Download missing collection archives (Premium). |
| `--skse-install` | Download the SKSE build matching the game from Nexus. |
| `--lang en` / `--lang uk` | Interface language (remembered). |
| `--profile NAME` / `--profiles` | Work with a profile (remembered) / list profiles. |
| `--profile-new NAME` [`--collection FILE` or `--collection-nexus LINK`] / `--profile-delete NAME` | New profile (all mods off, or the collection's mods on) / delete one. |

---

## SKSE on Steam Deck

**Do not add `skse64_loader.exe` to Steam as a separate non-Steam game.** It gets a new Proton prefix
with default settings (wrong game language → silent NPC voices, no Visual C++ runtime, a separate
`plugins.txt`). SKSE must start inside the game's own prefix.

### Option A — without SRS

`python3 mod_engine.py --skse-launcher` replaces `SkyrimSELauncher.exe` with SKSE (original backed up).
Steam's *Play* then starts SKSE. `--skse-launcher-restore` undoes it. After a Steam update or file
verification, run it again.

### Option B — with Skyrim Runtime Swapper (SRS, "Best of Both Worlds")

Some collections run the 1.6.1170 runtime on a 1.7.x install via SRS. SRS patches `SkyrimSE.exe`,
`SkyrimSELauncher.exe` and `Skyrim - Shaders.bsa` when SKSE starts, so the launcher must **not** be
replaced (the script refuses `--skse-launcher` when SRS is present). Instead, in
**Steam → Skyrim → Properties → Launch Options** put:

```
WINEDLLOVERRIDES="version=n,b" bash -c 'exec "${@/SkyrimSELauncher.exe/skse64_loader.exe}"' -- %command%
```

and select **Proton Experimental** under *Compatibility* (the SRS author tested with it). The first part
makes Proton use SRS's `version.dll`; the second starts `skse64_loader.exe` instead of the launcher.
`--skse` and `--deploy` check that these launch options are set.

### Visual C++ runtime (important)

If `--skse` shows many plugins with *"fatal error occurred while loading plugin"*, the prefix is using
Wine's built-in `msvcp140`/`vcruntime140`. Install Protontricks from Discover, close the game and run:

```
flatpak run com.github.Matoking.protontricks 489830 vcrun2022
```

On the test setup this took the result from 13 SKSE plugins failing with that error to none
(72 loaded; the only one left out was an old SE 1.5.97-only plugin that cannot load on this runtime).
The script detects this before you launch (Wine DLLs are marked internally, Microsoft's are not) and
also checks the matching `DllOverrides`.

---

## Nexus Mods account and API use

- **Signing in.** *Sign in with Nexus* (or `--nexus-login`) uses OAuth 2.0 with PKCE: the program opens the
  Nexus authorization page in your browser and receives the answer on a local address
  (`http://127.0.0.1:47361/oauth/callback`). There is no client secret in the program (a desktop app cannot
  keep one); PKCE protects the exchange. The access token is refreshed automatically; *Log out* revokes the
  refresh token and deletes it. This button appears once Nexus has registered the app; until then a
  **personal API key** can be used for testing (`--nexus-login KEY`).
- **What is stored, where.** Only on your computer: `~/.config/skyrim-mod-engine/nexus.json`, readable by
  your user only (mode 600) — the token or key, your user name and whether the account is Premium. Nothing
  is sent anywhere except to nexusmods.com.
- **When the program talks to Nexus.** Only when you do something: search the mod browser, open a mod's
  files, download, check for updates, attach a collection, install SKSE. Every request carries
  `Application-Name: Skyrim Mod Engine` and `Application-Version`. Nothing runs in the background.
- **Downloads.** With Premium, files are downloaded through the API. Without Premium only through the
  site's *Mod Manager Download* button (nxm:// links), as Nexus requires.
- **Update checks** read each mod's file list once (and once a month); in between only the mods Nexus
  lists as recently updated are re-read, to keep the number of requests low.

## Safety notes

- Close the game before `--deploy`, `--undeploy` and `--skse-launcher`; the script refuses while it runs.
- If another manager (e.g. Vortex) has deployed into the same game folder, purge it there first.
- Hard links mean a deployed file **is** the file in `Mods/`. Configs in `Data` are copied to avoid
  this, but files in the game folder itself (e.g. ENB `.ini`) are still linked: an in-place edit by the
  ENB editor changes the copy in `Mods/` too.
- If `Mods/` and the game are on different drives (e.g. game on the SD card, mods internal), hard links
  are impossible: files are copied, the script says how much space that takes and stops if it does not fit.
- Steam's *Verify integrity* restores vanilla files over deployed ones: run `--undeploy` and `--deploy` again.
- Turning off automatic Skyrim updates is recommended: a new game version breaks SKSE until it is updated.

## Known limitations

- Nexus collections: files bundled by the author inside the collection are not applied yet.
- The mod browser uses Nexus' GraphQL API v2, which Nexus calls work-in-progress; the query follows the
  one in Nexus' own Discord bot, and missing fields are dropped instead of failing.
- Collections: binary patches and generated files that Vortex adds from a collection are not applied.
  Tool outputs (BodySlide, animation tools) that are not shipped as normal mods are not created.
- LOOT support is a reimplementation: groups act as a sorting priority rather than a full constraint
  graph; conditions using `version()`/`product_version()` are skipped; Bash tags are not used.
  For a large load order, comparing once with real LOOT is a good idea.
- FOMOD "file is Active" conditions treat any installed plugin as active (no profiles yet).
- The SKSE plugin compatibility check is static (reads the DLL's export data). It matched a real
  `skse64.log` on the test setup; the log remains the final word.
- Mods that are only game-folder files are deployed; ENB/ReShade setup beyond copying files is up to you.

## Reporting problems

Send the lines containing `WARNING`, `ERROR`, `CRITICAL` or `NOT activated` (Ukrainian: `УВАГА`, `ПОМИЛКА`,
`КРИТИЧНО`, `НЕ активовано`), plus the
`=== SKSE ===` and `=== Proton / Steam ===` blocks from `--skse`. For crashes, the first ~80 lines of the
newest `crash-*.log` from Crash Logger
(`…/compatdata/489830/pfx/drive_c/users/steamuser/Documents/My Games/Skyrim Special Edition/SKSE/`).

---

## Credits

- Plugin sorting rules come from the **LOOT masterlist** (github.com/loot/skyrimse, CC0 1.0),
  downloaded at run time. LOOT itself (github.com/loot/loot) is not used or bundled.
- FOMOD install semantics (default destinations, priorities, ordering) were checked against the
  behaviour of **pyfomod**; no code from it is included.
- Skyrim Runtime Swapper, SKSE, Address Library and all mods belong to their authors.

## License

Copyright (C) 2026 MrGabenDima. Skyrim Mod Engine is free software under the
[GNU General Public License v3.0 or later](https://github.com/FartGarfild/SME-Skyrim-mod-engine/blob/main/LICENSE):
you may use, change and share it, including changed versions, as long as they stay under the same license
with their source code available and the author credited.
Source code: [github.com/FartGarfild/SME-Skyrim-mod-engine](https://github.com/FartGarfild/SME-Skyrim-mod-engine).
