"""Точка входа OFFLINE_REVIEW. Только локальный просмотр, replay, GUI и пилот."""
import sys
if not sys.flags.isolated:
    print('Use / используйте: python3 -I -B judge.py demo (или gui / pilot / verify)', file=sys.stderr)
    raise SystemExit(2)
sys.dont_write_bytecode = True
import argparse
import hashlib
import importlib.abc
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent


def no_network(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname', 'socket.gethostbyaddr', 'socket.bind'):
        raise PermissionError('OFFLINE_REVIEW: network disabled / сеть отключена')


def verify():
    manifest = json.loads((ROOT/'MANIFEST.json').read_text())
    if manifest.get('format') != 'reprocase-review-kit-v1' or manifest.get('status') != 'OFFLINE_REVIEW_BUILD':
        raise ValueError('Unknown bundle / неизвестный комплект')
    for name, item in manifest['files'].items():
        rel = Path(name)
        if rel.is_absolute() or '..' in rel.parts or '\\' in name:
            raise ValueError('Unsafe path / недопустимый путь')
        path = ROOT/rel
        if any(p.is_symlink() for p in (path, *path.parents) if p != ROOT.parent):
            raise ValueError('Symbolic link / символическая ссылка')
        data = path.read_bytes()
        if len(data) != item['bytes'] or hashlib.sha256(data).hexdigest() != item['sha256']:
            raise ValueError('Changed pinned file / изменён закреплённый файл: '+name)
    return manifest


class CheckedLoader(importlib.machinery.SourceFileLoader):
    def get_code(self, fullname):
        data = Path(self.path).read_bytes()
        if hashlib.sha256(data).hexdigest() != FILES['app/'+fullname+'.py']['sha256']:
            raise ValueError('Source changed before import / исходник изменён перед импортом')
        return self.source_to_code(data, self.path)


class CheckedFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        name = 'app/'+fullname+'.py'
        if name not in FILES:
            return None
        source = str(ROOT/name)
        return importlib.util.spec_from_file_location(fullname, source, loader=CheckedLoader(fullname, source))


def demo():
    results = []
    for label in ('success', 'repaired'):
        for version in ('before', 'after'):
            test = ROOT/'evidence'/label/'test_prepared.py'
            child = subprocess.run([sys.executable, '-I', '-S', '-B', str(test), '--version', version],
                                   cwd=ROOT, text=True, capture_output=True, timeout=20)
            counts = json.loads(child.stdout.strip().splitlines()[-1])
            expected = {'tests':1, 'failures':int(version=='before'), 'errors':0}
            passed = counts == expected and child.returncode == int(version=='before')
            results.append({'evidence':label, 'version':version, 'counts':counts, 'exit_code':child.returncode,
                            'expected_outcome_matched':passed})
            print(label, version, counts, 'ОЖИДАЕМО' if passed else 'ОШИБКА')
    output = ROOT/'review-output'; output.mkdir(exist_ok=True)
    record = {'source':'LOCAL_REPLAY_OF_SAVED_EVIDENCE', 'new_api_calls':0, 'runs':results,
              'original_partial_series':{'requested':6, 'attempted':2, 'valid_but_no_counterexample':1,
                                         'local_export_failure':1, 'not_started':4, 'confirmed_exports':0}}
    (output/'demo.json').write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    print('Сохранённый реальный ответ и отдельный ремонтный повтор; новых API-запросов нет.')
    print('Серия: 1 без находки, 1 локальная ошибка экспорта, 4 незапущены.')
    print('Словарь свойств ручной; преимущество ИИ и экономия труда не измерены.')
    print(output/'demo.json')
    return 0 if all(r['expected_outcome_matched'] for r in results) else 1


def gui(hidden=False):
    import desktop
    original = desktop.Window
    class OfflineWindow(original):
        def __init__(self, root):
            super().__init__(root)
            self.localizer.title = 'ReproCase — OFFLINE_REVIEW — OFFLINE'
            self.localizer.refresh()
            for button in (self.live_button, self.series_button, self.property_live_button):
                button.configure(state='disabled')
            self.status.set('OFFLINE_REVIEW: сеть отключена, ключ не нужен.')
        def start_live(self, *args, **kwargs):
            self.status.set('OFFLINE_REVIEW: отправка отключена.')
    desktop.Window = OfflineWindow
    if hidden:
        import tkinter as tk
        root = tk.Tk(); root.withdraw()
        try:
            window = OfflineWindow(root)
            for button in (window.live_button,window.series_button,window.property_live_button):
                assert str(button['state'])=='disabled'
            window.run_button.invoke(); root.update_idletasks()
            assert len(window.table.get_children())==2
            window.prepare_property()
            assert loc.text('НИЧЕГО НЕ ОТПРАВЛЕНО') in window.draft.get('1.0','end-1c')
            window.start_live(property_mode=True)
            assert 'отключена' in window.status.get()
            print('Скрытое окно: PASS; запросов 0')
        finally:
            root.destroy()
        return 0
    return desktop.main([])


def main():
    parser = argparse.ArgumentParser(description='OFFLINE_REVIEW — local judge kit / локальный комплект проверки')
    parser.add_argument('mode', choices=('verify','demo','gui','pilot','synthetic'))
    parser.add_argument('--hidden-check', action='store_true')
    parser.add_argument('--language', choices=('ru','en'), default=None)
    args = parser.parse_args()
    sys.addaudithook(no_network)
    global FILES, loc, print
    FILES = verify()['files']
    sys.meta_path.insert(0, CheckedFinder())
    import localization as loc
    if args.language:
        loc.cli_args(['--language', args.language])
    else:
        loc.set_language(loc.load_setting()[0] if args.mode=='gui' else 'ru')
    print = loc.print
    if args.mode=='verify':
        print('OFFLINE_REVIEW: хеши совпадают. Это не доказательство авторства.')
        return 0
    if args.mode=='demo': return demo()
    if args.mode=='gui': return gui(args.hidden_check)
    if args.mode=='pilot':
        import historical_pilot
        return historical_pilot.main([])
    import property_preparation
    return property_preparation.main(['--synthetic'])


if __name__=='__main__':
    try: raise SystemExit(main())
    except (ValueError,OSError,KeyError,subprocess.TimeoutExpired) as error:
        print('Bundle error / ошибка комплекта: '+str(error),file=sys.stderr)
        raise SystemExit(2)
