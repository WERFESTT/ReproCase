# Russian and English presentation

Use **Language / Язык: RU / EN** at the top right, next to Theme. Switching updates buttons, tabs, menus, statuses, explanations and the displayed result without recalculating, sending a request or discarding state. Only the language preference is stored in `language_settings.json`; the theme has a separate settings file. Neither contains a key.

For the optional underlying application, from the app/ directory:

```bash
python3 desktop.py --language en
python3 reprocase.py demo-all --language en
```

Use `ru` for Russian. Search, historical pilot, synthetic property preparation and verification commands also accept `--language ru|en`. In the separate offline review build:

```bash
python3 -I -B judge.py gui --language en
python3 -I -B judge.py demo --language en
```

CLI defaults to Russian; the GUI restores its saved preference unless explicitly overridden. Newly generated HTML/text reports use the selected run language. Existing reports are never silently translated or overwritten by switching the GUI language.

Translation is presentation only. JSON, machine fields, hashes, original responses, request contexts and historical evidence stay unchanged. The raw-data tab and original request JSON are explicitly labelled and may retain Russian content in EN mode. No model request is needed for translation. OS dialogs and Python/Tk/system messages may use their own language; the application does not change OS settings.

`python3 -B app/localization_gui_check.py` checks real widgets without displaying the window, if a display is available. Exit code 2 means display unavailable, not a successful visual check. During this review Tk could not connect to `:0`; no workaround or new screen recording was used.

For a short recording: choose **EN**, click **Check both versions**, show the result, then click **Test preparation preview**. These are local operations without API calls. Old Russian UI footage must not be presented as a recording of the English interface.
