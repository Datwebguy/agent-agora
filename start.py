import subprocess
import os


def start_server():
    venv_python = os.path.join(".venv", "Scripts", "python.exe") if os.name == "nt" else os.path.join(".venv", "bin", "python")

    if not os.path.exists(venv_python):
        print("Error: .venv not found. Run 'uv sync' first.")
        return

    try:
        subprocess.run([venv_python, "-m", "uvicorn", "market_service:app", "--reload", "--port", "8000"])
    except KeyboardInterrupt:
        print("\nStopping server...")


if __name__ == "__main__":
    start_server()
