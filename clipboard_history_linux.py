#!/usr/bin/env /usr/bin/python3
import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version('Gdk', '3.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gdk, Gtk

DB_PATH = Path.home() / '.local/share/zeitgeist/activity.sqlite'
DEFAULT_LIMIT = 300
COL_TIME = 0
COL_PREVIEW = 1
COL_TEXT = 2
COL_ID = 3


def query_history(limit: int, search: str | None = None):
    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute(
        '''
        SELECT
            timestamp,
            datetime(timestamp/1000, 'unixepoch', 'localtime') AS local_time,
            COALESCE(subj_current_uri, subj_uri) AS item_id,
            subj_text,
            actor_uri
        FROM event_view
        WHERE subj_text IS NOT NULL
          AND trim(subj_text) != ''
          AND event_origin_uri = 'application://diodon.desktop'
        ORDER BY timestamp DESC
        LIMIT ?
        ''',
        (max(limit * 5, 500),),
    ).fetchall()
    conn.close()

    deduped = []
    seen = set()
    search_lower = search.lower() if search else None

    for row in rows:
        text = row['subj_text']
        item_id = row['item_id'] or text
        if item_id in seen:
            continue
        if search_lower and search_lower not in text.lower():
            continue
        seen.add(item_id)
        deduped.append(
            {
                'item_id': item_id,
                'timestamp': row['timestamp'],
                'local_time': row['local_time'],
                'text': text,
                'preview': build_preview(text),
                'source': row['actor_uri'] or '',
            }
        )
        if len(deduped) >= limit:
            break
    return deduped


def build_preview(text: str, width: int = 120) -> str:
    single_line = ' '.join(text.split())
    if len(single_line) <= width:
        return single_line
    return single_line[: width - 1] + '…'


def copy_text(text: str):
    for selection in ('clipboard', 'primary'):
        subprocess.run(
            ['xclip', '-selection', selection],
            input=text,
            text=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )


def dump_items(items):
    for idx, item in enumerate(items, start=1):
        print(f"{idx:03d}\t{item['local_time']}\t{item['preview']}")


class HistoryWindow(Gtk.Window):
    def __init__(self, items):
        super().__init__(title='剪贴板完整历史')
        self.items = items
        self.search_text = ''
        self.set_default_size(1280, 860)
        self.set_border_width(10)
        self.connect('destroy', Gtk.main_quit)
        self.connect('key-press-event', self.on_key_press)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.add(root)

        header = Gtk.Label(
            label='支持鼠标滚轮、触摸板双指滚动、PgUp/PgDn、方向键。双击或回车即可恢复历史项。'
        )
        header.set_xalign(0)
        root.pack_start(header, False, False, 0)

        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text('搜索历史内容…')
        self.search_entry.connect('search-changed', self.on_search_changed)
        root.pack_start(self.search_entry, False, False, 0)

        pane = Gtk.Paned.new(Gtk.Orientation.VERTICAL)
        root.pack_start(pane, True, True, 0)

        self.store = Gtk.ListStore(str, str, str, str)
        for item in items:
            self.store.append([item['local_time'], item['preview'], item['text'], item['item_id']])

        self.filter_model = self.store.filter_new()
        self.filter_model.set_visible_func(self.filter_visible)

        self.tree = Gtk.TreeView(model=self.filter_model)
        self.tree.set_headers_visible(True)
        self.tree.set_enable_search(True)
        self.tree.set_search_column(COL_PREVIEW)
        self.tree.connect('row-activated', self.on_row_activated)

        renderer_time = Gtk.CellRendererText()
        column_time = Gtk.TreeViewColumn('时间', renderer_time, text=COL_TIME)
        column_time.set_resizable(True)
        column_time.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
        self.tree.append_column(column_time)

        renderer_preview = Gtk.CellRendererText()
        renderer_preview.set_property('wrap-mode', 2)
        renderer_preview.set_property('wrap-width', 850)
        column_preview = Gtk.TreeViewColumn('内容预览', renderer_preview, text=COL_PREVIEW)
        column_preview.set_expand(True)
        column_preview.set_resizable(True)
        self.tree.append_column(column_preview)

        selection = self.tree.get_selection()
        selection.connect('changed', self.on_selection_changed)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        scroll.add(self.tree)
        pane.pack1(scroll, resize=True, shrink=False)

        self.detail_view = Gtk.TextView()
        self.detail_view.set_editable(False)
        self.detail_view.set_cursor_visible(False)
        self.detail_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        detail_scroll = Gtk.ScrolledWindow()
        detail_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        detail_scroll.set_hexpand(True)
        detail_scroll.set_vexpand(True)
        detail_scroll.add(self.detail_view)
        pane.pack2(detail_scroll, resize=True, shrink=False)
        pane.set_position(520)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        root.pack_start(actions, False, False, 0)

        self.status_label = Gtk.Label(label=f'共加载 {len(items)} 条历史')
        self.status_label.set_xalign(0)
        actions.pack_start(self.status_label, True, True, 0)

        copy_button = Gtk.Button(label='恢复所选项')
        copy_button.connect('clicked', self.on_copy_clicked)
        actions.pack_start(copy_button, False, False, 0)

        close_button = Gtk.Button(label='关闭')
        close_button.connect('clicked', lambda *_: Gtk.main_quit())
        actions.pack_start(close_button, False, False, 0)

        self.select_first_row()
        self.tree.grab_focus()

    def filter_visible(self, model, tree_iter, _data=None):
        if not self.search_text:
            return True
        preview = (model[tree_iter][COL_PREVIEW] or '').lower()
        full_text = (model[tree_iter][COL_TEXT] or '').lower()
        return self.search_text in preview or self.search_text in full_text

    def on_search_changed(self, entry):
        self.search_text = entry.get_text().strip().lower()
        self.filter_model.refilter()
        self.update_status()
        self.select_first_row()

    def update_status(self):
        visible = len(self.filter_model)
        if self.search_text:
            self.status_label.set_text(f'筛选后 {visible} 条结果')
        else:
            self.status_label.set_text(f'共加载 {visible} 条历史')

    def select_first_row(self):
        if len(self.filter_model) == 0:
            self.detail_view.get_buffer().set_text('')
            return
        path = Gtk.TreePath.new_from_indices([0])
        self.tree.set_cursor(path)
        self.tree.scroll_to_cell(path, None, False, 0, 0)
        self.update_detail_from_path(path)

    def update_detail_from_path(self, path):
        tree_iter = self.filter_model.get_iter(path)
        if tree_iter is None:
            self.detail_view.get_buffer().set_text('')
            return
        self.detail_view.get_buffer().set_text(self.filter_model[tree_iter][COL_TEXT] or '')

    def on_selection_changed(self, selection):
        model, tree_iter = selection.get_selected()
        if tree_iter is None:
            self.detail_view.get_buffer().set_text('')
            return
        self.detail_view.get_buffer().set_text(model[tree_iter][COL_TEXT] or '')

    def copy_selected(self):
        selection = self.tree.get_selection()
        model, tree_iter = selection.get_selected()
        if tree_iter is None:
            self.status_label.set_text('请先选择一条历史记录')
            return
        text = model[tree_iter][COL_TEXT] or ''
        copy_text(text)
        Gtk.main_quit()

    def on_copy_clicked(self, _button):
        self.copy_selected()

    def on_row_activated(self, _tree, path, _column):
        self.update_detail_from_path(path)
        self.copy_selected()

    def on_key_press(self, _widget, event):
        if event.keyval == Gdk.KEY_Escape:
            Gtk.main_quit()
            return True
        if event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.copy_selected()
            return True
        if event.state & Gdk.ModifierType.CONTROL_MASK and event.keyval in (Gdk.KEY_f, Gdk.KEY_F):
            self.search_entry.grab_focus()
            return True
        return False


def parse_args():
    parser = argparse.ArgumentParser(description='Browse Diodon clipboard history with a scrollable GTK window.')
    parser.add_argument('--limit', type=int, default=DEFAULT_LIMIT)
    parser.add_argument('--dump', action='store_true', help='Print recent history and exit.')
    parser.add_argument('--copy', type=int, help='Copy the Nth history item from the current result set.')
    parser.add_argument('--search', help='Filter history entries by substring before showing the list.')
    return parser.parse_args()


def main():
    args = parse_args()
    items = query_history(args.limit, args.search)

    if args.dump:
        dump_items(items)
        return 0

    if not items:
        print('当前没有可显示的剪贴板历史。', file=sys.stderr)
        return 0

    if args.copy:
        index = args.copy - 1
        if index < 0 or index >= len(items):
            print('Invalid index', file=sys.stderr)
            return 1
        copy_text(items[index]['text'])
        return 0

    window = HistoryWindow(items)
    window.show_all()
    Gtk.main()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
