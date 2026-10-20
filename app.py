from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_from_directory
import os
import zipfile
import subprocess
import signal
import shutil
import json
from datetime import datetime
import sys
import time
import threading
import atexit

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "master_hosting_gold_secret_2024")

UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", "servers")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

processes = {}


# ---------- Cleanup ----------
def force_delete_directory(path, max_retries=5, delay=1):
    for i in range(max_retries):
        try:
            if os.path.exists(path):
                shutil.rmtree(path, ignore_errors=True)
                return True
        except Exception as e:
            print(f"Attempt {i+1} failed: {e}")
            time.sleep(delay)
    return False


@atexit.register
def cleanup_on_exit():
    for (username, server_name), process in list(processes.items()):
        try:
            if process.poll() is None:
                process.terminate()
                time.sleep(0.5)
                if process.poll() is None:
                    process.kill()
        except Exception:
            pass


# ---------- Helpers ----------
def get_user_server_path():
    if 'username' not in session:
        return None
    user_dir = os.path.join(UPLOAD_FOLDER, session['username'])
    os.makedirs(user_dir, exist_ok=True)
    return user_dir


def extract_zip(zip_path, extract_to):
    with zipfile.ZipFile(zip_path, 'r') as z:
        z.extractall(extract_to)


def install_requirements(path):
    req = os.path.join(path, "requirements.txt")
    if os.path.exists(req):
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "-r", req],
                capture_output=True, text=True, timeout=300
            )
        except Exception as e:
            print(f"Requirements error: {e}")


def find_main_file(path):
    common = ["main.py", "app.py", "bot.py", "server.py", "index.py", "start.py"]
    for f in common:
        if os.path.exists(os.path.join(path, f)):
            return f
    for root, dirs, files in os.walk(path):
        for file in files:
            if file.endswith('.py') and not file.startswith('_'):
                fp = os.path.join(root, file)
                try:
                    with open(fp, 'r', encoding='utf-8') as fh:
                        if '__main__' in fh.read():
                            return file
                except Exception:
                    continue
    return None


def save_server_config(username, server_name, config):
    p = os.path.join(UPLOAD_FOLDER, username, server_name, "config.json")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f:
        json.dump(config, f, indent=2)


def load_server_config(username, server_name):
    p = os.path.join(UPLOAD_FOLDER, username, server_name, "config.json")
    if os.path.exists(p):
        try:
            with open(p, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {"status": "stopped", "type": "web", "port": 8080,
            "created_at": str(datetime.now())}


def start_server(server_name):
    try:
        user_dir = get_user_server_path()
        if not user_dir:
            return False
        server_dir = os.path.join(user_dir, server_name)
        if not os.path.exists(server_dir):
            return False
        config = load_server_config(session['username'], server_name)
        log_path = os.path.join(server_dir, "logs.txt")

        with open(log_path, 'a', encoding='utf-8') as log:
            log.write(f"\n{'='*60}\n")
            log.write(f"Starting {server_name} at {datetime.now()}\n")
            zip_path = os.path.join(server_dir, "server.zip")
            extract_dir = os.path.join(server_dir, "extracted")

            if os.path.exists(zip_path):
                if not os.path.exists(extract_dir):
                    os.makedirs(extract_dir, exist_ok=True)
                    try:
                        extract_zip(zip_path, extract_dir)
                        install_requirements(extract_dir)
                    except Exception as e:
                        log.write(f"Extract error: {e}\n")
                working_dir = extract_dir
            else:
                working_dir = server_dir
                install_requirements(working_dir)

            main_file = find_main_file(working_dir)
            if not main_file:
                test_file = os.path.join(working_dir, "test_server.py")
                if not os.path.exists(test_file):
                    with open(test_file, 'w') as f:
                        f.write(
                            "from flask import Flask\nimport time\n"
                            "app = Flask(__name__)\n"
                            "@app.route('/')\n"
                            "def home():\n"
                            "    return f'<h1>MASTER HOSTING</h1><p>{time.ctime()}</p>'\n"
                            "if __name__ == '__main__':\n"
                            "    app.run(host='0.0.0.0', port=5000)\n"
                        )
                main_file = "test_server.py"

            python_cmd = "python3" if shutil.which("python3") else "python"
            cmd = [python_cmd, main_file]
            log.write(f"Command: {' '.join(cmd)}\nWorking: {working_dir}\n{'='*60}\n")

        log_file = open(log_path, 'a', encoding='utf-8')
        try:
            p = subprocess.Popen(
                cmd, cwd=working_dir, stdout=log_file, stderr=log_file,
                shell=False, start_new_session=True
            )
            processes[(session['username'], server_name)] = p
            config['status'] = 'running'
            config['pid'] = p.pid
            config['started_at'] = str(datetime.now())
            save_server_config(session['username'], server_name, config)

            def monitor(proc, key, uname, sname):
                proc.wait()
                processes.pop(key, None)
                c = load_server_config(uname, sname)
                c['status'] = 'stopped'
                c.pop('pid', None)
                save_server_config(uname, sname, c)

            threading.Thread(target=monitor,
                             args=(p, (session['username'], server_name),
                                   session['username'], server_name),
                             daemon=True).start()
            return True
        except Exception as e:
            log_file.write(f"[ERROR] {e}\n")
            log_file.close()
            return False
    except Exception as e:
        print(f"[start_server] {e}")
        return False


def stop_server(server_name):
    key = (session['username'], server_name)
    p = processes.get(key)
    if p:
        try:
            p.terminate()
            time.sleep(2)
            if p.poll() is None:
                p.kill()
                time.sleep(1)
            processes.pop(key, None)
            config = load_server_config(session['username'], server_name)
            config['status'] = 'stopped'
            config.pop('pid', None)
            save_server_config(session['username'], server_name, config)
            return True
        except Exception as e:
            print(f"[stop_server] {e}")
            return False
    return True


# ---------- Routes ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        if username and len(username) >= 3:
            session['username'] = username
            return redirect(url_for("dashboard"))
        return render_template("login.html", error="Username must be 3+ characters")
    return render_template("login.html")


@app.route("/logout")
def logout():
    if 'username' in session:
        username = session['username']
        to_stop = [(u, n) for (u, n) in list(processes.keys()) if u == username]
        for (u, sname) in to_stop:
            stop_server(sname)
        time.sleep(1)
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET", "POST"])
def dashboard():
    if 'username' not in session:
        return redirect(url_for("login"))

    user_dir = get_user_server_path()

    if request.method == "POST":
        action = request.form.get("action")
        if action == "create_server":
            server_name = request.form.get("server_name", "").strip()
            server_type = request.form.get("server_type", "web")
            port = request.form.get("port", "8080")

            if server_name:
                safe_name = (server_name.replace(" ", "_")
                             .replace("/", "_").replace("\\", "_"))
                server_dir = os.path.join(user_dir, safe_name)

                if not os.path.exists(server_dir):
                    os.makedirs(server_dir, exist_ok=True)
                    config = {
                        "name": server_name,
                        "display_name": server_name,
                        "safe_name": safe_name,
                        "type": server_type,
                        "port": int(port) if port.isdigit() else 8080,
                        "status": "stopped",
                        "created_at": str(datetime.now())
                    }
                    save_server_config(session['username'], safe_name, config)

                    file = request.files.get("server_files")
                    if file and file.filename:
                        if file.filename.endswith(".zip"):
                            file.save(os.path.join(server_dir, "server.zip"))
                        else:
                            file.save(os.path.join(server_dir, file.filename))

    servers = []
    if os.path.exists(user_dir):
        for folder in os.listdir(user_dir):
            sdir = os.path.join(user_dir, folder)
            if not os.path.isdir(sdir):
                continue
            config = load_server_config(session['username'], folder)
            log_file = os.path.join(sdir, "logs.txt")
            log_data = ""
            if os.path.exists(log_file):
                try:
                    with open(log_file, 'r', errors='ignore', encoding='utf-8') as f:
                        c = f.read()
                        log_data = c[-2000:] if len(c) > 2000 else c
                except Exception:
                    log_data = "Error reading logs"

            has_files = (os.path.exists(os.path.join(sdir, "server.zip")) or
                         any(f.endswith('.py') for f in os.listdir(sdir)
                             if os.path.isfile(os.path.join(sdir, f))))

            servers.append({
                "name": folder,
                "display_name": config.get("display_name", folder),
                "running": (session['username'], folder) in processes,
                "log": log_data,
                "config": config,
                "has_files": has_files,
                "created_at": config.get("created_at", "Unknown")
            })

    servers.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return render_template("dashboard.html", servers=servers)


@app.route("/api/server/<action>/<name>", methods=["POST"])
def server_action(action, name):
    if 'username' not in session:
        return jsonify({"error": "Not authenticated"}), 401

    server_name = name.strip()
    user_dir = get_user_server_path()
    server_dir = os.path.join(user_dir, server_name) if user_dir else None

    if action == "start":
        if not server_dir or not os.path.exists(server_dir):
            return jsonify({"error": f"Server '{server_name}' not found"}), 404
        if (session['username'], server_name) in processes:
            return jsonify({"error": "Already running"}), 400
        if start_server(server_name):
            return jsonify({"success": True, "message": f"'{server_name}' started"})
        return jsonify({"error": "Failed to start. Check logs."}), 400

    elif action == "stop":
        if stop_server(server_name):
            return jsonify({"success": True, "message": f"'{server_name}' stopped"})
        return jsonify({"error": "Failed to stop"}), 400

    elif action == "restart":
        stop_server(server_name)
        time.sleep(2)
        if start_server(server_name):
            return jsonify({"success": True, "message": f"'{server_name}' restarted"})
        return jsonify({"error": "Failed to restart"}), 400

    elif action == "delete":
        stop_server(server_name)
        time.sleep(1)
        if server_dir and os.path.exists(server_dir):
            if force_delete_directory(server_dir):
                processes.pop((session['username'], server_name), None)
                return jsonify({"success": True, "message": f"'{server_name}' deleted"})
            return jsonify({"error": "Delete failed"}), 400
        return jsonify({"error": "Not found"}), 404

    return jsonify({"error": "Invalid action"}), 400


@app.route("/api/logs/<name>")
def get_logs(name):
    if 'username' not in session:
        return "", 401
    user_dir = get_user_server_path()
    log_file = os.path.join(user_dir, name.strip(), "logs.txt")
    if os.path.exists(log_file):
        try:
            with open(log_file, 'r', errors='ignore', encoding='utf-8') as f:
                c = f.read()
                return c[-10000:] if len(c) > 10000 else c
        except Exception:
            return "Error reading log file"
    return "No logs available"


@app.route("/api/servers")
def get_servers():
    if 'username' not in session:
        return jsonify([])
    user_dir = get_user_server_path()
    servers = []
    if os.path.exists(user_dir):
        for folder in os.listdir(user_dir):
            if os.path.isdir(os.path.join(user_dir, folder)):
                config = load_server_config(session['username'], folder)
                servers.append({
                    "name": folder,
                    "display_name": config.get("display_name", folder),
                    "running": (session['username'], folder) in processes,
                    "config": config
                })
    return jsonify(servers)


@app.route("/api/stats")
def get_stats():
    if 'username' not in session:
        return jsonify({})
    user_dir = get_user_server_path()
    total = running = 0
    if os.path.exists(user_dir):
        for folder in os.listdir(user_dir):
            if os.path.isdir(os.path.join(user_dir, folder)):
                total += 1
                if (session['username'], folder) in processes:
                    running += 1
    return jsonify({
        "total_servers": total,
        "running_servers": running,
        "unlimited": True,
        "message": "Unlimited servers available!"
    })


@app.route("/health")
def health_check():
    return jsonify({"status": "healthy", "timestamp": str(datetime.now()),
                    "total_processes": len(processes)})


@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


if __name__ == "__main__":
    os.makedirs("templates", exist_ok=True)
    port = int(os.environ.get("PORT", 8030))
    print(f"""
    ╔══════════════════════════════════════════╗
    ║        MASTER HOSTING — GOLD EDITION     ║
    ║         Unlimited Servers · Free         ║
    ╠══════════════════════════════════════════╣
    ║  • http://0.0.0.0:{port}
    ║  • Upload folder: {UPLOAD_FOLDER}
    ╚══════════════════════════════════════════╝
    """)
    app.run(host="0.0.0.0", port=port, debug=False)