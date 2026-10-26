"""Hidden-window RU/EN acceptance; uses only application widgets and fake transport."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
from unittest import mock
import desktop
import localization as loc
import desktop_theme
import nebius_client


def main():
    try:
        root=tk.Tk()
    except tk.TclError as error:
        print('GUI_CHECK_UNAVAILABLE: '+str(error))
        return 2
    root.withdraw()
    try:
        with tempfile.TemporaryDirectory(dir=desktop.app.ROOT/'outputs') as temp, \
             mock.patch.object(loc,'SETTINGS_PATH',Path(temp)/'language.json'), \
             mock.patch.object(desktop_theme,'SETTINGS_PATH',Path(temp)/'theme.json'), \
             mock.patch.object(nebius_client,'_https_once',side_effect=AssertionError('NETWORK_FORBIDDEN')) as send:
            w=desktop.Window(root)
            for language in ('en','ru','en'):
                w.language_choice.set(language.upper());w.change_language()
                for theme in ('dark','light'):
                    w.theme_choice.set(desktop_theme.LABELS[theme]);w.change_theme()
                    for case in desktop.CATALOG:
                        w.session.choose(case);w.populate();w.run_button.invoke()
                        result=w.session.evidence
                        w.language_choice.set('RU' if language=='en' else 'EN');w.change_language()
                        assert w.session.evidence is result
                        w.language_choice.set(language.upper());w.change_language()
                        root.geometry(f'{root.minsize()[0]}x820');root.update_idletasks()
                        for button in (w.run_button,w.reset_button,w.load_button,w.save_button,w.copy_button,
                                       w.prepare_button,w.find_button,w.property_live_button,w.property_preview_button):
                            assert button.winfo_x()+button.winfo_reqwidth() <= button.master.winfo_width()
                        w.copy_button.invoke()
                        assert root.clipboard_get()==w.explanation.get('1.0','end-1c')
                        with mock.patch('tkinter.filedialog.askopenfilename',return_value=''):
                            w.load_button.invoke()
                        assert w.session.evidence is result
                    w.prepare_property()
                    with mock.patch('tkinter.messagebox.askyesno',return_value=False) as consent:
                        w.start_live(property_mode=True)
                    assert not w.live_pending
                    assert loc.text('Разрешение на реальный запрос')==consent.call_args.args[0]
            send.assert_not_called()
            assert json.loads((Path(temp)/'language.json').read_text())=={'language':'en'}
            print('RU_EN_THEME_COPY_CANCEL_LAYOUT: PASS; real API calls: 0; no visual claim')
    finally:
        root.destroy()
    return 0

if __name__=='__main__':raise SystemExit(main())
