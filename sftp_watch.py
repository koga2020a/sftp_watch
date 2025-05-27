import argparse
import yaml
import configparser
import base64
import os
import time
import sys
import socket
import json
import csv
import re
import msvcrt
import threading
from paramiko import Transport, SFTPClient, RSAKey
from stat import S_ISDIR
from collections import defaultdict
from datetime import datetime, timedelta

# カラー出力（Git Bash 用）
RESET = '\033[0m'
BLUE = '\033[94m'
WHITE = '\033[97m'
CYAN = '\033[96m'  # 水色
ORANGE = '\033[93m'  # オレンジ色
RED = '\033[91m'    # 赤色
GREEN = '\033[92m'  # 緑色
YELLOW = '\033[93m' # 黄色
MAGENTA = '\033[95m' # マゼンタ

# 色のマッピング
COLOR_MAP = {
    'RED': RED,
    'GREEN': GREEN,
    'BLUE': BLUE,
    'YELLOW': YELLOW,
    'CYAN': CYAN,
    'MAGENTA': MAGENTA,
    'WHITE': WHITE,
    'ORANGE': ORANGE
}

def apply_color_to_string(text, string_colors):
    """
    設定に基づいて文字列中のマッチ部分だけに色を適用する
    """
    if not string_colors:
        return text

    # 1) 単純一致の処理：部分文字列を赤くするなど
    for match in string_colors.get('exact_matches', []):
        s = match['string']
        color = COLOR_MAP.get(match['color'], WHITE)
        # 大文字小文字を無視してマッチした部分だけ色付け
        pattern = re.escape(s)
        replacement = f"{color}{s}{RESET}"
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    # 2) 正規表現の処理：パターンにマッチした範囲だけ色付け
    for match in string_colors.get('regex_matches', []):
        pat = match['pattern']
        color = COLOR_MAP.get(match['color'], WHITE)
        # グループ化して置換
        text = re.sub(f"({pat})", lambda m: f"{color}{m.group(1)}{RESET}", text)

    return text

def parse_args():
    parser = argparse.ArgumentParser(description='SFTP Directory Monitor with tree, color, logging')
    parser.add_argument('--config', type=str, help='Path to config file (yaml or ini)')
    return parser.parse_args()

def load_config(path):
    if path.endswith(('.yaml', '.yml')):
        with open(path, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    elif path.endswith('.ini'):
        cfg = configparser.ConfigParser()
        cfg.read(path, encoding='utf-8')
        return {s: dict(cfg.items(s)) for s in cfg.sections()}
    else:
        raise ValueError('Unsupported config format')

def create_proxy_socket(proxy_host, proxy_port, target_host, target_port):
    sock = socket.create_connection((proxy_host, proxy_port))
    req = f"CONNECT {target_host}:{target_port} HTTP/1.1\r\nHost: {target_host}:{target_port}\r\n\r\n"
    sock.sendall(req.encode())
    resp = b''
    while b'\r\n\r\n' not in resp:
        chunk = sock.recv(4096)
        if not chunk: break
        resp += chunk
    if b'200 Connection established' not in resp:
        raise ConnectionError(f"Proxy CONNECT failed: {resp!r}")
    return sock

def connect_sftp(cfg):
    use_proxy = cfg.get('proxy_host') and cfg.get('proxy_port')
    if use_proxy:
        print(f"[INFO] Connecting via proxy {cfg['proxy_host']}:{cfg['proxy_port']}")
        sock = create_proxy_socket(cfg['proxy_host'], int(cfg['proxy_port']), cfg['host'], int(cfg['port']))
        transport = Transport(sock)
    else:
        print(f"[INFO] Connecting directly to {cfg['host']}:{cfg['port']}")
        transport = Transport((cfg['host'], int(cfg['port'])))

    if cfg['auth_type'] == 'pkey':
        key_data = base64.b64decode(cfg['pkey_base64'])
        with open('tmp_key.pem', 'wb') as f:
            f.write(key_data)
        pkey = RSAKey.from_private_key_file('tmp_key.pem')
        os.remove('tmp_key.pem')
        transport.connect(username=cfg['user'], pkey=pkey)
    else:
        transport.connect(username=cfg['user'], password=cfg['password'])

    return SFTPClient.from_transport(transport)

def list_files_recursive(sftp, directory):
    entries = {}
    try:
        for entry in sftp.listdir_attr(directory):
            name = entry.filename
            if name.startswith('.'):
                continue
            path = os.path.join(directory, name).replace('\\', '/')
            if S_ISDIR(entry.st_mode):
                entries[path + '/'] = {
                    'is_dir': True
                }
                entries.update(list_files_recursive(sftp, path))
            else:
                entries[path] = {
                    'size': entry.st_size,
                    'mtime': entry.st_mtime,
                    'is_dir': False
                }
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[ERROR] Listing failed in {directory}: {e}")
    return entries

def print_tree(entries, string_colors=None):
    tree = defaultdict(list)
    for p in sorted(entries):
        parts = p.strip('/').split('/')
        parent = '/'.join(parts[:-1])
        tree[parent].append(p)

    def _recurse(parent, level):
        for p in tree.get(parent, []):
            indent = '  ' * level
            name = p.rstrip('/').split('/')[-1]
            is_dir = entries[p]['is_dir']
            base_color = BLUE if is_dir else WHITE
            size = entries[p].get('size')
            size_str = '' if is_dir else f' size={size:,}'
            
            # 基本の色を適用
            colored_name = f"{base_color}{name}{RESET}"
            if string_colors:
                # config.yamlの設定を適用
                colored_name = apply_color_to_string(colored_name, string_colors)
            
            print(f"{indent}{colored_name}{'/' if is_dir else ''}{size_str}")
            if is_dir:
                _recurse(p.strip('/'), level+1)

    _recurse('', 0)

def write_logs_csv(changes):
    """
    変更をCSVファイルに書き込む
    """
    # 現在の日付を取得
    today = datetime.now().strftime('%Y%m%d')
    
    # /logディレクトリが存在しない場合は作成
    log_dir = 'log'
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
    
    # 日付付きのログファイル名
    date_log_file = os.path.join(log_dir, f'log-{today}.csv')
    
    # 両方のファイルに書き込み
    for log_file in ['log.csv', date_log_file]:
        with open(log_file, 'a', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            for row in changes:
                w.writerow(row)

def format_elapsed_time(seconds):
    if seconds < 60:
        return f"{seconds}秒"
    elif seconds < 3600:
        minutes = seconds // 60
        seconds = seconds % 60
        return f"{minutes}分{seconds}秒"
    else:
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        seconds = seconds % 60
        return f"{hours}時間{minutes}分{seconds}秒"

def write_display_log(timestamp, messages, last_change_time=None, string_colors=None):
    with open('display_log.txt', 'a', encoding='utf-8') as f:
        f.write(f"\n------------------\n")
        elapsed_str = ""
        if last_change_time:
            elapsed = (datetime.strptime(timestamp, '%Y-%m-%d %H:%M:%S') - last_change_time).total_seconds()
            elapsed_str = f" (経過: {format_elapsed_time(int(elapsed))})"
        
        # タイムスタンプに色を適用
        colored_timestamp = apply_color_to_string(timestamp, string_colors)
        f.write(f"-----  {colored_timestamp}  変更検知{elapsed_str}  -----\n")
        
        for msg in messages:
            # メッセージに色を適用
            colored_msg = apply_color_to_string(msg, string_colors)
            f.write(f"{colored_msg}\n")
        f.write("\n")

def write_logs_json(state):
    with open('log.json', 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, ensure_ascii=False)

def get_dir_paths(path_dict):
    """
    Extract only directory paths from the entries dictionary
    """
    return {p.rstrip('/'): True for p in path_dict if path_dict[p]['is_dir']}

def get_user_input():
    """
    ユーザー入力を取得する関数
    Windowsコンソールでの文字化けを防ぐため、バイトとして読み取り
    """
    try:
        key = msvcrt.getch()
        if key == b'\xe0':  # 特殊キーの場合
            key = msvcrt.getch()  # 2バイト目を読み取り
            return None
        try:
            return key.decode('cp932')  # Windowsの日本語環境用
        except UnicodeDecodeError:
            return key.decode('ascii', errors='ignore')  # フォールバック
    except Exception as e:
        print(f"[ERROR] Input error: {e}")
        return None

def write_memo_log(memo):
    """
    メモをログファイルに書き込む
    """
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    # memo_log.txtに書き込み
    with open('memo_log.txt', 'a', encoding='utf-8') as f:
        f.write(f"[{now}] {memo}\n")
    
    # log.csvと日付付きログファイルにも追記
    changes = [[now, 'MEMO', memo]]
    write_logs_csv(changes)

def get_memo_input():
    """
    メモ入力を取得する
    """
    print("\nメモを入力してください（Enterで確定、Escでキャンセル）:")
    memo = []
    while True:
        key = msvcrt.getch()
        if key == b'\r':  # Enter
            break
        elif key == b'\x1b':  # Esc
            return None
        elif key == b'\x08':  # Backspace
            if memo:
                memo.pop()
                sys.stdout.write('\b \b')
                sys.stdout.flush()
        else:
            try:
                char = key.decode('cp932')
                memo.append(char)
                sys.stdout.write(char)
                sys.stdout.flush()
            except UnicodeDecodeError:
                pass
    print()  # 改行
    return ''.join(memo)

def main():
    args = parse_args()
    cfg_path = args.config or 'config.yaml'
    if not os.path.exists(cfg_path):
        print(f"[ERROR] Config not found: {cfg_path}")
        sys.exit(1)
    cfg = load_config(cfg_path)
    if isinstance(cfg, dict) and 'default' in cfg:
        cfg = cfg['default']

    # 文字列と色の設定を取得
    string_colors = cfg.get('string_colors', {})

    for k in ('host','port','user','auth_type','dirs'):
        if k not in cfg:
            print(f"[ERROR] Missing config: {k}")
            sys.exit(1)
    cfg['port'] = int(cfg.get('port',22))
    cfg['interval'] = int(cfg.get('interval',60))
    if isinstance(cfg['dirs'], str):
        cfg['dirs'] = [d.strip() for d in cfg['dirs'].split(',')]

    print("[INFO] Connecting to SFTP...")
    print(f"[INFO] host         = {cfg['host']}")
    print(f"[INFO] port         = {cfg['port']}")
    print(f"[INFO] user         = {cfg['user']}")
    print(f"[INFO] password     = {'*'*8 if cfg.get('password') else '(none)'}")
    print(f"[INFO] auth_type    = {cfg['auth_type']}")
    print(f"[INFO] proxy_host   = {cfg.get('proxy_host','(none)')}")
    print(f"[INFO] proxy_port   = {cfg.get('proxy_port','(none)')}")
    print(f"[INFO] dirs         = {cfg['dirs']}")
    print(f"[INFO] interval     = {cfg['interval']}")
    if cfg['auth_type']=='pkey':
        l = len(cfg.get('pkey_base64',''))
        print(f"[INFO] pkey_base64 = {'*'*10}... ({l} chars)")

    sftp = connect_sftp(cfg)
    print(f"[INFO] Monitoring recursively: {cfg['dirs']} every {cfg['interval']}s")
    print("[INFO] Press 'm' to add a memo")

    prev = {}
    prev_dirs = {}  # Store directory paths separately
    first = True
    last_change_time = None

    # ユーザー入力用のスレッドを開始
    def input_handler():
        while True:
            key = get_user_input()
            if key and key.lower() == 'm':
                memo = get_memo_input()
                if memo:
                    write_memo_log(memo)
                    print(f"[INFO] メモを保存しました: {memo}")

    input_thread = threading.Thread(target=input_handler)
    input_thread.daemon = True
    input_thread.start()

    try:
        while True:
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            current = {}
            for d in cfg['dirs']:
                current.update(list_files_recursive(sftp, d))

            # Extract directory paths from current entries
            current_dirs = get_dir_paths(current)

            if first:
                print(f"[INFO] Initial scan complete. {len(current)} entries:")
                print_tree(current, string_colors)
                print("-"*40)
            else:
                # Process regular file changes
                file_entries = {p: v for p, v in current.items() if not v['is_dir']}
                prev_file_entries = {p: v for p, v in prev.items() if not v['is_dir']}
                
                added_files = set(file_entries) - set(prev_file_entries)
                removed_files = set(prev_file_entries) - set(file_entries)
                
                # Find files with same name and size but different modification time
                common_files = set(file_entries) & set(prev_file_entries)
                updated = set()
                date_only_changed = set()
                
                for p in common_files:
                    if file_entries[p]['size'] != prev_file_entries[p]['size']:
                        updated.add(p)
                    elif file_entries[p]['mtime'] != prev_file_entries[p]['mtime']:
                        date_only_changed.add(p)
                
                # Process directory changes - only report when a directory is actually added or removed
                added_dirs = set(current_dirs) - set(prev_dirs)
                removed_dirs = set(prev_dirs) - set(current_dirs)
                
                if added_files or removed_files or updated or date_only_changed or added_dirs or removed_dirs:
                    print("\n------------------")
                    elapsed_str = ""
                    if last_change_time:
                        elapsed = (datetime.strptime(now, '%Y-%m-%d %H:%M:%S') - last_change_time).total_seconds()
                        elapsed_str = f" (経過: {format_elapsed_time(int(elapsed))})"
                    print(f"-----  {now}  変更検知{elapsed_str}  -----")

                    changes = []
                    display_messages = []
                    
                    # Report added directories (only genuinely new ones)
                    for p in sorted(added_dirs):
                        name = p.rstrip('/').split('/')[-1]
                        base_msg = f"{BLUE}[ADD] {RESET} {p.replace(name, '')}{BLUE}{name}/{RESET} (directory)"
                        msg = apply_color_to_string(base_msg, string_colors) if string_colors else base_msg
                        print(msg)
                        display_messages.append(msg.replace(BLUE, '').replace(RESET, ''))
                        changes.append([now, 'ADD_DIR', p + '/'])
                    
                    # Report removed directories (only genuinely removed ones)
                    for p in sorted(removed_dirs):
                        name = p.rstrip('/').split('/')[-1]
                        base_msg = f"{BLUE}[DEL] {RESET} {p.replace(name, '')}{BLUE}{name}/{RESET} (directory)"
                        msg = apply_color_to_string(base_msg, string_colors) if string_colors else base_msg
                        print(msg)
                        display_messages.append(msg.replace(BLUE, '').replace(RESET, ''))
                        changes.append([now, 'DEL_DIR', p + '/'])
                    
                    # Report added files
                    for p in sorted(added_files):
                        m = current[p]
                        name = p.split('/')[-1]
                        base_msg = f"{CYAN}[ADD] {RESET} {p.replace(name, '')}{CYAN}{name}{RESET} size={m['size']:,}"
                        msg = apply_color_to_string(base_msg, string_colors) if string_colors else base_msg
                        print(msg)
                        display_messages.append(msg.replace(CYAN, '').replace(RESET, ''))
                        changes.append([now, 'ADD', p, m['size']])
                    
                    # Report removed files
                    for p in sorted(removed_files):
                        m = prev[p]
                        name = p.split('/')[-1]
                        base_msg = f"{CYAN}[DEL] {RESET} {p.replace(name, '')}{CYAN}{name}{RESET} was size={m['size']:,}"
                        msg = apply_color_to_string(base_msg, string_colors) if string_colors else base_msg
                        print(msg)
                        display_messages.append(msg.replace(CYAN, '').replace(RESET, ''))
                        changes.append([now, 'DEL', p, m['size']])
                    
                    # Report modified files
                    for p in sorted(updated):
                        name = p.split('/')[-1]
                        base_msg = f"{ORANGE}[MOD] {RESET} {p.replace(name, '')}{ORANGE}{name}{RESET} {prev[p]['size']:,} -> {current[p]['size']:,}"
                        msg = apply_color_to_string(base_msg, string_colors) if string_colors else base_msg
                        print(msg)
                        display_messages.append(msg.replace(ORANGE, '').replace(RESET, ''))
                        changes.append([now, 'MOD', p, prev[p]['size'], current[p]['size']])
                        
                    # Report files with only date changes
                    for p in sorted(date_only_changed):
                        old_time = datetime.fromtimestamp(prev[p]['mtime']).strftime('%Y-%m-%d %H:%M:%S')
                        new_time = datetime.fromtimestamp(current[p]['mtime']).strftime('%Y-%m-%d %H:%M:%S')
                        file_size = current[p]['size']
                        name = p.split('/')[-1]
                        base_msg = f"{ORANGE}[DATE]{RESET} {p.replace(name, '')}{ORANGE}{name}{RESET} size={file_size:,} {old_time} -> {new_time}"
                        msg = apply_color_to_string(base_msg, string_colors) if string_colors else base_msg
                        print(msg)
                        display_messages.append(msg.replace(ORANGE, '').replace(RESET, ''))
                        changes.append([now, 'DATE', p, file_size, old_time, new_time])

                    # Write both CSV and display logs
                    write_logs_csv(changes)
                    write_display_log(now, display_messages, last_change_time, string_colors)
                    last_change_time = datetime.strptime(now, '%Y-%m-%d %H:%M:%S')

            write_logs_json(current)
            prev = current
            prev_dirs = current_dirs
            first = False
            
            # 監視間隔を1秒ずつに分割して、より頻繁にキー入力をチェック
            for _ in range(cfg['interval']):
                time.sleep(1)
                # キー入力チェックは別スレッドで行われているため、
                # ここでは特に追加の処理は不要です

    finally:
        sftp.close()

if __name__ == '__main__':
    main()
