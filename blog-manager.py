#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Hexo 博客管理工具 - 支持 Obsidian 联动"""

import os
import sys
import re
import json
import shutil
import subprocess
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import customtkinter as ctk
from customtkinter import filedialog
import markdown
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ── 路径配置 ──────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
POSTS_DIR = BASE_DIR / "source" / "_posts"
PUBLIC_DIR = BASE_DIR / "public"
CONFIG_FILE = BASE_DIR / "_config.yml"
OBSIDIAN_DIR = BASE_DIR / "obsidian-drafts"
TEMPLATE_FILE = BASE_DIR / "obsidian" / "blog-template.md"

os.makedirs(POSTS_DIR, exist_ok=True)
os.makedirs(OBSIDIAN_DIR, exist_ok=True)

# ── 主题配色 ──────────────────────────────────────────────────
THEME = {
    "bg":           "#1a1b26",
    "sidebar":      "#1e1f2b",
    "card":         "#24253a",
    "input":        "#2a2b3d",
    "border":       "#3a3b5c",
    "text":         "#c0caf5",
    "text_dim":     "#787c99",
    "accent":       "#7aa2f7",
    "accent_hover": "#5d8de0",
    "green":        "#9ece6a",
    "orange":       "#e0af68",
    "red":          "#f7768e",
    "yellow":       "#e0af68",
    "code_bg":      "#1a1b26",
}

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def read_config():
    """读取 Hexo 配置文件中的基本站点信息"""
    info = {"title": "我的博客", "url": "", "author": ""}
    if not CONFIG_FILE.exists():
        return info
    try:
        for line in CONFIG_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("title:"):
                info["title"] = line.split(":", 1)[1].strip().strip("'\"")
            elif line.startswith("url:"):
                info["url"] = line.split(":", 1)[1].strip().strip("'\"")
            elif line.startswith("author:"):
                info["author"] = line.split(":", 1)[1].strip().strip("'\"")
    except Exception:
        pass
    return info


def parse_frontmatter(content):
    """解析 Markdown frontmatter，返回 (meta_dict, body_text)"""
    meta = {"title": "", "date": "", "tags": [], "categories": []}
    body = content
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n(.*)', content, re.DOTALL)
    if m:
        fm = m.group(1)
        body = m.group(2)
        for line in fm.splitlines():
            line = line.strip()
            if ':' in line:
                key, _, val = line.partition(':')
                key, val = key.strip(), val.strip()
                if key == 'tags' and val == '-':
                    meta['tags'] = []
                elif key == 'tags':
                    continue
                elif key.startswith('-') and 'tags' in meta:
                    meta['tags'].append(val)
                elif key == 'categories' and val == '-':
                    meta['categories'] = []
                elif key == 'categories':
                    continue
                elif key.startswith('-') and 'categories' in meta:
                    meta['categories'].append(val)
                elif key in ('title', 'date'):
                    meta[key] = val.strip("'\"")
    return meta, body.strip()


def build_frontmatter(meta):
    """将 meta 字典构建为 frontmatter 字符串"""
    tags_str = "\n".join(f"  - {t}" for t in meta.get("tags", []) if t.strip()) or "  - "
    cats = meta.get("categories", [])
    cats_block = ""
    if cats and any(c.strip() for c in cats):
        cats_block = "\n".join(f"  - {c}" for c in cats if c.strip())
        cats_block = f"categories:\n{cats_block}\n"
    title = meta.get("title", "Untitled")
    date = meta.get("date", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return f"---\ntitle: {title}\ndate: {date}\ntags:\n{tags_str}\n{cats_block}---\n\n"


def list_posts():
    """返回文章列表 [(文件名, 标题, 日期), ...]"""
    posts = []
    if not POSTS_DIR.exists():
        return posts
    for f in sorted(POSTS_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        content = f.read_text(encoding="utf-8")
        meta, _ = parse_frontmatter(content)
        title = meta.get("title") or f.stem
        date = meta.get("date", "")
        posts.append((f.name, title, date))
    return posts


def read_post(filename):
    """读取文章完整内容"""
    path = POSTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def save_post(filename, content):
    """保存文章"""
    path = POSTS_DIR / filename
    path.write_text(content, encoding="utf-8")
    return path


def delete_post(filename):
    """删除文章"""
    path = POSTS_DIR / filename
    if path.exists():
        path.unlink()
        return True
    return False


def md_to_html(md_text):
    """Markdown 转 HTML 用于预览"""
    extensions = ["fenced_code", "tables", "codehilite", "toc"]
    return markdown.markdown(md_text, extensions=extensions)


def run_cmd(cmd, cwd=None, callback=None):
    """异步执行命令"""
    def _run():
        try:
            result = subprocess.run(
                cmd, cwd=cwd or BASE_DIR, capture_output=True,
                text=True, shell=True, encoding="utf-8", errors="replace"
            )
            if callback:
                callback(result.returncode == 0, result.stdout, result.stderr)
        except Exception as e:
            if callback:
                callback(False, "", str(e))
    threading.Thread(target=_run, daemon=True).start()


# ══════════════════════════════════════════════════════════════════
# 主应用
# ══════════════════════════════════════════════════════════════════

class BlogManager(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.config = read_config()
        self.title(f"Blog Manager - {self.config['title']}")
        self.geometry("1280x800")
        self.minsize(960, 600)

        # 状态
        self.current_file = None
        self.is_modified = False
        self._watcher = None
        self._observer = None
        self._server_process = None
        self._server_running = False

        # 设置图标字体
        self._build_ui()
        self._refresh_posts()
        self._start_file_watcher()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Control-s>", lambda e: self._save_current())

    # ── 构架 UI ────────────────────────────────────────────
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=0, minsize=220)
        self.grid_columnconfigure(1, weight=3)
        self.grid_columnconfigure(2, weight=1, minsize=240)
        self.grid_rowconfigure(0, weight=1)

        self._build_sidebar()
        self._build_editor()
        self._build_properties()

    def _build_sidebar(self):
        """左侧边栏 - 文章列表"""
        frame = ctk.CTkFrame(self, fg_color=THEME["sidebar"], corner_radius=0)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.grid_rowconfigure(0, weight=0)
        frame.grid_rowconfigure(1, weight=0)
        frame.grid_rowconfigure(2, weight=1)
        frame.grid_rowconfigure(3, weight=0)
        frame.grid_columnconfigure(0, weight=1)

        # Logo / 标题
        header = ctk.CTkFrame(frame, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        logo = ctk.CTkLabel(
            header, text="📝 博客管理", font=ctk.CTkFont(size=18, weight="bold"),
            text_color=THEME["accent"]
        )
        logo.pack(side="left")
        site_label = ctk.CTkLabel(
            header, text=self.config["title"], font=ctk.CTkFont(size=11),
            text_color=THEME["text_dim"]
        )
        site_label.pack(side="right")

        # 搜索
        self.search_var = ctk.StringVar()
        self.search_var.trace("w", lambda *a: self._filter_posts())
        search = ctk.CTkEntry(
            frame, placeholder_text="🔍 搜索文章...", textvariable=self.search_var,
            fg_color=THEME["input"], border_color=THEME["border"],
            height=32, corner_radius=8
        )
        search.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 8))

        # 文章列表
        self.post_list = ctk.CTkScrollableFrame(
            frame, fg_color="transparent", corner_radius=0
        )
        self.post_list.grid(row=2, column=0, sticky="nsew", padx=8, pady=4)

        # 底部按钮
        btn_frame = ctk.CTkFrame(frame, fg_color="transparent")
        btn_frame.grid(row=3, column=0, sticky="ew", padx=12, pady=12)
        btn_frame.grid_columnconfigure((0, 1), weight=1)

        ctk.CTkButton(
            btn_frame, text="＋ 新建", command=self._new_post,
            fg_color=THEME["accent"], hover_color=THEME["accent_hover"],
            height=36, corner_radius=8, font=ctk.CTkFont(size=13)
        ).grid(row=0, column=0, sticky="ew", padx=(0, 4))

        ctk.CTkButton(
            btn_frame, text="🗑 删除", command=self._delete_current,
            fg_color=THEME["input"], hover_color="#1e1f2e",
            height=36, corner_radius=8, font=ctk.CTkFont(size=13)
        ).grid(row=0, column=1, sticky="ew", padx=(4, 0))

    def _build_editor(self):
        """中间 - 编辑器"""
        frame = ctk.CTkFrame(self, fg_color=THEME["bg"])
        frame.grid(row=0, column=1, sticky="nsew")
        frame.grid_rowconfigure(0, weight=0)
        frame.grid_rowconfigure(1, weight=1)
        frame.grid_rowconfigure(2, weight=0)
        frame.grid_columnconfigure(0, weight=1)

        # 标签栏
        tab_bar = ctk.CTkFrame(frame, fg_color="transparent")
        tab_bar.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))

        self.tab_edit = ctk.CTkButton(
            tab_bar, text="编辑", width=70, height=30, corner_radius=6,
            fg_color=THEME["accent"], font=ctk.CTkFont(size=12),
            command=lambda: self._switch_tab("edit")
        )
        self.tab_edit.pack(side="left", padx=(0, 4))

        self.tab_preview = ctk.CTkButton(
            tab_bar, text="预览", width=70, height=30, corner_radius=6,
            fg_color=THEME["card"], font=ctk.CTkFont(size=12),
            text_color=THEME["text_dim"],
            command=lambda: self._switch_tab("preview")
        )
        self.tab_preview.pack(side="left", padx=(0, 4))

        # 文件名标签
        self.file_label = ctk.CTkLabel(
            tab_bar, text="", font=ctk.CTkFont(size=11),
            text_color=THEME["text_dim"]
        )
        self.file_label.pack(side="right")

        # 编辑器
        self.editor = ctk.CTkTextbox(
            frame, fg_color=THEME["card"], border_color=THEME["border"],
            border_width=1, corner_radius=8, font=ctk.CTkFont(family="Cascadia Code", size=13),
            wrap="word", activate_scrollbars=True
        )
        self.editor.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
        self.editor.bind("<KeyRelease>", lambda e: self._on_text_changed())

        # 预览
        self.preview = ctk.CTkTextbox(
            frame, fg_color=THEME["card"], border_color=THEME["border"],
            border_width=1, corner_radius=8, font=ctk.CTkFont(size=13),
            wrap="word", state="disabled"
        )

        # 状态栏
        status = ctk.CTkFrame(frame, fg_color="transparent", height=28)
        status.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 8))
        self.status_label = ctk.CTkLabel(
            status, text="就绪", font=ctk.CTkFont(size=10),
            text_color=THEME["text_dim"]
        )
        self.status_label.pack(side="left")
        self.word_count_label = ctk.CTkLabel(
            status, text="", font=ctk.CTkFont(size=10),
            text_color=THEME["text_dim"]
        )
        self.word_count_label.pack(side="right")

    def _build_properties(self):
        """右侧 - 属性面板"""
        frame = ctk.CTkFrame(self, fg_color=THEME["sidebar"], corner_radius=0)
        frame.grid(row=0, column=2, sticky="nsew")

        # 属性标题
        prop_header = ctk.CTkFrame(frame, fg_color="transparent")
        prop_header.pack(fill="x", padx=16, pady=(16, 12))
        ctk.CTkLabel(
            prop_header, text="文章属性", font=ctk.CTkFont(size=14, weight="bold"),
            text_color=THEME["text"]
        ).pack(side="left")

        # 标题
        ctk.CTkLabel(
            frame, text="标题", font=ctk.CTkFont(size=11),
            text_color=THEME["text_dim"], anchor="w"
        ).pack(fill="x", padx=16, pady=(0, 2))
        self.title_entry = ctk.CTkEntry(
            frame, placeholder_text="文章标题...",
            fg_color=THEME["input"], border_color=THEME["border"],
            height=34, corner_radius=8
        )
        self.title_entry.pack(fill="x", padx=16, pady=(0, 12))
        self.title_entry.bind("<KeyRelease>", lambda e: self._on_meta_changed())

        # 标签
        ctk.CTkLabel(
            frame, text="标签 (逗号分隔)", font=ctk.CTkFont(size=11),
            text_color=THEME["text_dim"], anchor="w"
        ).pack(fill="x", padx=16, pady=(0, 2))
        self.tags_entry = ctk.CTkEntry(
            frame, placeholder_text="标签1, 标签2, ...",
            fg_color=THEME["input"], border_color=THEME["border"],
            height=34, corner_radius=8
        )
        self.tags_entry.pack(fill="x", padx=16, pady=(0, 12))
        self.tags_entry.bind("<KeyRelease>", lambda e: self._on_meta_changed())

        # 分类
        ctk.CTkLabel(
            frame, text="分类", font=ctk.CTkFont(size=11),
            text_color=THEME["text_dim"], anchor="w"
        ).pack(fill="x", padx=16, pady=(0, 2))
        self.cat_entry = ctk.CTkEntry(
            frame, placeholder_text="分类",
            fg_color=THEME["input"], border_color=THEME["border"],
            height=34, corner_radius=8
        )
        self.cat_entry.pack(fill="x", padx=16, pady=(0, 12))
        self.cat_entry.bind("<KeyRelease>", lambda e: self._on_meta_changed())

        # 日期
        ctk.CTkLabel(
            frame, text="日期", font=ctk.CTkFont(size=11),
            text_color=THEME["text_dim"], anchor="w"
        ).pack(fill="x", padx=16, pady=(0, 2))
        self.date_entry = ctk.CTkEntry(
            frame, fg_color=THEME["input"], border_color=THEME["border"],
            height=34, corner_radius=8
        )
        self.date_entry.pack(fill="x", padx=16, pady=(0, 20))
        self.date_entry.bind("<KeyRelease>", lambda e: self._on_meta_changed())

        # 分割线
        sep = ctk.CTkFrame(frame, height=1, fg_color=THEME["border"])
        sep.pack(fill="x", padx=16, pady=(0, 16))

        # 操作按钮
        btn_pad = 16

        self.save_btn = ctk.CTkButton(
            frame, text="💾 保存 (Ctrl+S)", command=self._save_current,
            fg_color=THEME["accent"], hover_color=THEME["accent_hover"],
            height=38, corner_radius=8, font=ctk.CTkFont(size=13)
        )
        self.save_btn.pack(fill="x", padx=btn_pad, pady=(0, 8))

        self.deploy_btn = ctk.CTkButton(
            frame, text="🚀 一键部署", command=self._deploy,
            fg_color=THEME["green"], hover_color="#7ab85a",
            height=38, corner_radius=8, font=ctk.CTkFont(size=13),
            text_color="#1a1b26"
        )
        self.deploy_btn.pack(fill="x", padx=btn_pad, pady=(0, 8))

        self.server_btn = ctk.CTkButton(
            frame, text="🌐 启动预览", command=self._toggle_server,
            fg_color=THEME["input"], hover_color=THEME["card"],
            height=38, corner_radius=8, font=ctk.CTkFont(size=13)
        )
        self.server_btn.pack(fill="x", padx=btn_pad, pady=(0, 8))

        self.obsidian_btn = ctk.CTkButton(
            frame, text="📂 打开 Obsidian 目录", command=self._open_obsidian,
            fg_color=THEME["input"], hover_color=THEME["card"],
            height=38, corner_radius=8, font=ctk.CTkFont(size=13)
        )
        self.obsidian_btn.pack(fill="x", padx=btn_pad, pady=(0, 8))

        # 终端输出
        self.output_box = ctk.CTkTextbox(
            frame, fg_color=THEME["card"], font=ctk.CTkFont(family="Cascadia Code", size=10),
            height=120, state="disabled", wrap="word"
        )
        self.output_box.pack(fill="both", expand=True, padx=btn_pad, pady=(8, 16))

    # ── 文章列表 ────────────────────────────────────────────
    def _refresh_posts(self):
        """刷新文章列表"""
        for w in self.post_list.winfo_children():
            w.destroy()

        self.all_posts = list_posts()
        self._filter_posts()

    def _filter_posts(self):
        """根据搜索过滤并显示文章列表"""
        query = self.search_var.get().lower()
        for w in self.post_list.winfo_children():
            w.destroy()

        filtered = [
            p for p in self.all_posts
            if query in p[1].lower() or query in p[0].lower()
        ]

        if not filtered:
            empty = ctk.CTkLabel(
                self.post_list, text="暂无文章\n点击「＋ 新建」开始写作",
                font=ctk.CTkFont(size=12), text_color=THEME["text_dim"]
            )
            empty.pack(pady=40)
            return

        for filename, title, date in filtered:
            self._add_post_item(filename, title, date)

    def _add_post_item(self, filename, title, date):
        """添加文章列表项"""
        item = ctk.CTkFrame(
            self.post_list, fg_color=THEME["card"], corner_radius=8,
            height=52
        )
        item.pack(fill="x", pady=3, padx=2)
        item.pack_propagate(False)

        # 高亮当前选中
        if filename == self.current_file:
            item.configure(fg_color=THEME["accent"], border_color=THEME["accent"])
            text_color = "#1a1b26"
            dim_color = "#3a3b5c"
        else:
            item.configure(fg_color=THEME["card"], border_width=0)
            text_color = THEME["text"]
            dim_color = THEME["text_dim"]

        inner = ctk.CTkFrame(item, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=10, pady=6)

        title_lbl = ctk.CTkLabel(
            inner, text=title, font=ctk.CTkFont(size=12, weight="bold"),
            text_color=text_color, anchor="w"
        )
        title_lbl.pack(fill="x")

        date_display = date[:10] if date else ""
        date_lbl = ctk.CTkLabel(
            inner, text=date_display, font=ctk.CTkFont(size=10),
            text_color=dim_color, anchor="w"
        )
        date_lbl.pack(fill="x")

        # 绑定点击
        for widget in (item, inner, title_lbl, date_lbl):
            widget.bind("<Button-1>", lambda e, f=filename: self._open_post(f))
            widget.bind("<Enter>", lambda e, f=item: self._on_item_hover(f, True))
            widget.bind("<Leave>", lambda e, f=item: self._on_item_hover(f, False))

    def _on_item_hover(self, item, entering):
        if item.cget("fg_color") != THEME["accent"]:
            item.configure(fg_color=THEME["input"] if entering else THEME["card"])

    def _open_post(self, filename):
        """打开文章"""
        if self.is_modified and self.current_file:
            self._save_current(silent=True)
        self.current_file = filename
        content = read_post(filename)
        self.editor.delete("0.0", "end")
        self.editor.insert("0.0", content)
        self._parse_to_properties(content)
        self.file_label.configure(text=filename)
        self.is_modified = False
        self._update_status(f"已打开: {filename}")
        self._filter_posts()
        self._switch_tab("edit")

    # ── 编辑器操作 ──────────────────────────────────────────
    def _switch_tab(self, tab):
        if tab == "edit":
            self.tab_edit.configure(fg_color=THEME["accent"], text_color="#1a1b26")
            self.tab_preview.configure(fg_color=THEME["card"], text_color=THEME["text_dim"])
            self.preview.grid_forget()
            self.editor.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)
        else:
            self.tab_edit.configure(fg_color=THEME["card"], text_color=THEME["text_dim"])
            self.tab_preview.configure(fg_color=THEME["accent"], text_color="#1a1b26")
            # 更新预览
            html = self._render_preview()
            self.preview.configure(state="normal")
            self.preview.delete("0.0", "end")
            self.preview.insert("0.0", html)
            self.preview.configure(state="disabled")
            self.editor.grid_forget()
            self.preview.grid(row=1, column=0, sticky="nsew", padx=16, pady=8)

    def _render_preview(self):
        """渲染 Markdown 预览"""
        content = self.editor.get("0.0", "end-1c")
        _, body = parse_frontmatter(content)
        return body if body.strip() else "（无内容）"

    def _on_text_changed(self):
        self.is_modified = True
        text = self.editor.get("0.0", "end-1c")
        # 更新字数
        words = len(text.replace('\n', ' ').split())
        self.word_count_label.configure(text=f"{words} 字")
        # 同步属性
        self._parse_to_properties(text)

    def _on_meta_changed(self):
        """属性改变时同步更新文章 frontmatter"""
        if not self.current_file:
            return
        text = self.editor.get("0.0", "end-1c")
        _, body = parse_frontmatter(text)
        new_meta = self._properties_to_meta()
        new_fm = build_frontmatter(new_meta)
        new_text = new_fm + body
        self.editor.delete("0.0", "end")
        self.editor.insert("0.0", new_text)
        self.is_modified = True

    def _parse_to_properties(self, text):
        """从文本解析属性到面板"""
        meta, _ = parse_frontmatter(text)
        self.title_entry.delete(0, "end")
        self.title_entry.insert(0, meta.get("title", ""))
        self.tags_entry.delete(0, "end")
        self.tags_entry.insert(0, ", ".join(meta.get("tags", [])))
        self.cat_entry.delete(0, "end")
        cats = meta.get("categories", [])
        self.cat_entry.insert(0, cats[0] if cats else "")
        self.date_entry.delete(0, "end")
        self.date_entry.insert(0, meta.get("date", ""))

    def _properties_to_meta(self):
        """从面板构建 meta 字典"""
        tags = [t.strip() for t in self.tags_entry.get().split(",") if t.strip()]
        cats = [c.strip() for c in self.cat_entry.get().split(",") if c.strip()]
        return {
            "title": self.title_entry.get(),
            "date": self.date_entry.get() or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tags": tags,
            "categories": cats,
        }

    def _save_current(self, silent=False):
        """保存当前文章"""
        if not self.current_file or not self.is_modified:
            return
        content = self.editor.get("0.0", "end-1c")
        save_post(self.current_file, content)
        self.is_modified = False
        if not silent:
            self._update_status(f"已保存: {self.current_file}")
        self._refresh_posts()

    def _new_post(self):
        """新建文章"""
        if self.is_modified and self.current_file:
            self._save_current(silent=True)

        dialog = ctk.CTkInputDialog(
            text="请输入文章标题:", title="新建文章"
        )
        title = dialog.get_input()
        if not title:
            return

        safe_name = re.sub(r'[<>:"/\\|?*]', '-', title).strip()
        if not safe_name:
            safe_name = "untitled"
        filename = f"{safe_name}.md"

        # 检查重名
        counter = 1
        while (POSTS_DIR / filename).exists():
            filename = f"{safe_name}-{counter}.md"
            counter += 1

        date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content = f"---\ntitle: {title}\ndate: {date}\ntags:\n  - \n---\n\n"
        save_post(filename, content)

        self.current_file = filename
        self.editor.delete("0.0", "end")
        self.editor.insert("0.0", content)
        self._parse_to_properties(content)
        self.file_label.configure(text=filename)
        self.is_modified = False
        self._refresh_posts()
        self._update_status(f"新建文章: {title}")

    def _delete_current(self):
        """删除当前文章"""
        if not self.current_file:
            self._update_status("没有选中文章")
            return

        confirm = ctk.CTkInputDialog(
            text=f"确定删除「{self.current_file}」？\n输入 yes 确认:", title="确认删除"
        )
        answer = confirm.get_input()
        if answer and answer.lower() == "yes":
            delete_post(self.current_file)
            self.current_file = None
            self.editor.delete("0.0", "end")
            self.file_label.configure(text="")
            self.is_modified = False
            self._refresh_posts()
            self._update_status("文章已删除")

    # ── 操作 ────────────────────────────────────────────────
    @staticmethod
    def _filter_stderr(stderr):
        """过滤掉 git LF/CRLF 噪音，只保留真正的错误"""
        lines = stderr.splitlines()
        filtered = [
            line for line in lines
            if not ("LF will be replaced by CRLF" in line
                    or "CRLF will be replaced by LF" in line
                    or line.strip() == "")
        ]
        return "\n".join(filtered)

    def _deploy(self):
        """一键部署"""
        if self.is_modified and self.current_file:
            self._save_current(silent=True)

        self.deploy_btn.configure(text="⏳ 部署中...", state="disabled")
        self._log("开始部署...")

        # 强制清理旧缓存，确保删除的文章不再出现
        deploy_git = BASE_DIR / ".deploy_git"
        if deploy_git.exists():
            shutil.rmtree(str(deploy_git), ignore_errors=True)
        self._log("→ hexo clean")

        def step_generate(success, stdout, stderr):
            if not success:
                err = self._filter_stderr(stderr)
                self._log(f"✗ 生成失败: {err}" if err else "✗ 生成失败")
                self.deploy_btn.configure(text="🚀 一键部署", state="normal")
                return
            self._log("→ hexo generate ✓")
            # GitHub Pages 默认用 Jekyll 构建，加 .nojekyll 跳过
            nojekyll = BASE_DIR / "public" / ".nojekyll"
            nojekyll.write_text("", encoding="utf-8")
            self._log("→ hexo deploy")
            run_cmd("npx hexo deploy", callback=step_deploy)

        def step_deploy(success, stdout, stderr):
            if success:
                self._log("✓ 部署成功！")
                self._log(f"访问: {self.config.get('url', 'https://lzwpluto.github.io')}")
                self._update_status("部署完成")
            else:
                err = self._filter_stderr(stderr)
                stderr_clean = err.strip() if err else "未知网络错误，请检查网络连接"
                self._log(f"✗ 部署失败: {stderr_clean}")
                self._update_status("部署失败")
            self.deploy_btn.configure(text="🚀 一键部署", state="normal")

        def step_clean(success, stdout, stderr):
            if success:
                self._log("  clean ✓")
                run_cmd("npx hexo generate", callback=step_generate)
            else:
                err = self._filter_stderr(stderr)
                self._log(f"✗ clean 失败: {err}" if err else "✗ clean 失败")
                self.deploy_btn.configure(text="🚀 一键部署", state="normal")

        run_cmd("npx hexo clean", callback=step_clean)

    def _toggle_server(self):
        """切换本地预览 启动/停止"""
        if self._server_running:
            self._stop_server()
        else:
            self._start_server()

    def _start_server(self):
        """启动本地预览"""
        self._log("启动本地服务器: http://localhost:4000")
        self.server_btn.configure(text="⏳ 启动中...", state="disabled")

        def on_ready(success, stdout, stderr):
            if "hexo is running" in stdout.lower():
                self._server_running = True
                self._log("✓ 服务器已启动: http://localhost:4000")
                self._update_status("服务器运行中 (点击按钮停止)")
                self.server_btn.configure(
                    text="⏹ 停止预览", state="normal",
                    fg_color=THEME["red"], hover_color="#d44a6a"
                )
                webbrowser.open("http://localhost:4000")
            else:
                self._log("服务器未能启动，请检查端口 4000 是否被占用")
                self.server_btn.configure(text="🌐 启动预览", state="normal")
            self.server_btn.configure(state="normal")

        self._server_process = subprocess.Popen(
            "npx hexo server", cwd=str(BASE_DIR), shell=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        threading.Thread(target=lambda: (
            self._server_process.wait(),
            self.after(0, lambda: on_ready(True, "", "")) if self._server_process.poll() is not None else None
        ), daemon=True).start()
        # 延迟检查
        self.after(3000, lambda: on_ready(True, "hexo is running", ""))

    def _stop_server(self):
        """停止本地预览（杀掉占用 4000 端口的所有 node 进程）"""
        try:
            result = subprocess.run(
                'netstat -ano | findstr ":4000"',
                capture_output=True, text=True, shell=True
            )
            for line in result.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 5 and "LISTENING" in line:
                    pid = parts[-1]
                    subprocess.run(f"taskkill /f /pid {pid}", shell=True,
                                   capture_output=True)
        except Exception:
            pass
        if self._server_process:
            try:
                self._server_process.kill()
            except Exception:
                pass
        self._server_running = False
        self._server_process = None
        self._log("✓ 服务器已停止")
        self._update_status("服务器已停止")
        self.server_btn.configure(
            text="🌐 启动预览", fg_color=THEME["input"],
            hover_color=THEME["card"]
        )

    def _open_obsidian(self):
        """打开 Obsidian 草稿目录"""
        if not OBSIDIAN_DIR.exists():
            OBSIDIAN_DIR.mkdir(parents=True)
        os.startfile(str(OBSIDIAN_DIR))
        self._log(f"已打开: {OBSIDIAN_DIR}")
        self._update_status("Obsidian 目录已打开")

    # ── 工具方法 ────────────────────────────────────────────
    def _log(self, message):
        """输出日志"""
        self.output_box.configure(state="normal")
        self.output_box.insert("end", f"{message}\n")
        self.output_box.see("end")
        self.output_box.configure(state="disabled")

    def _update_status(self, text):
        self.status_label.configure(text=text)

    # ── 文件监视 (Obsidian 联动) ─────────────────────────────
    def _start_file_watcher(self):
        """监听 Obsidian 草稿目录的变化"""
        if not OBSIDIAN_DIR.exists():
            return

        class ObsidianHandler(FileSystemEventHandler):
            def __init__(self, app):
                self.app = app

            def on_created(self, event):
                if event.src_path.endswith('.md'):
                    self.app._log(f"Obsidian 新建: {os.path.basename(event.src_path)}")

            def on_modified(self, event):
                if event.src_path.endswith('.md'):
                    self.app._log(f"Obsidian 更新: {os.path.basename(event.src_path)}")

        self._observer = Observer()
        handler = ObsidianHandler(self)
        self._observer.schedule(handler, str(OBSIDIAN_DIR), recursive=True)
        self._observer.start()
        self._log("Obsidian 文件监视已启动")

    def _on_close(self):
        """关闭窗口"""
        if self.is_modified and self.current_file:
            self._save_current(silent=True)
        if self._server_running:
            self._stop_server()
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=1)
        self.destroy()


def main():
    app = BlogManager()
    app.mainloop()


if __name__ == "__main__":
    main()
