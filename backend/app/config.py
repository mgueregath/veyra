from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LABELHUB_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://labelhub:labelhub_dev@127.0.0.1:55433/labelhub"
    secret_key: str = "cambia-esto-en-produccion"
    # Los archivos pesados (videos, imágenes) NO van en la base: se resuelven contra esta raíz.
    media_root: Path = Path("media")
    export_root: Path = Path("exports")  # aquí se escriben los datasets exportados (YOLO/COCO)
    # Entrenamiento e inferencia: procesos aparte con un Python que tenga PyTorch (la API no lo necesita)
    train_python: str = "/usr/bin/python3"
    train_script: Path = Path(__file__).resolve().parents[2] / "workers" / "train_yolo.py"
    predict_script: Path = Path(__file__).resolve().parents[2] / "workers" / "predict_yolo.py"
    pretrained_dirs: str = "/home/uach/DEV/cupper-utils:/home/uach/DEV/lice/models-comparative:/home/uach/models"
    models_root: Path = Path("models")
    runs_root: Path = Path("runs")
    device: str = "0"
    # detección en vivo
    live_script: Path = Path(__file__).resolve().parents[2] / "workers" / "live_yolo.py"
    live_python: str = ""  # vacío = train_python
    live_detector: str = ""  # "fake" / "none" fuerza ese detector (pruebas y demostraciones)
    live_start_timeout: float = 120.0  # segundos para cargar el modelo y abrir la fuente
    live_max: int = 2  # sesiones simultáneas (cada una ocupa la GPU y la CPU)
    live_allowed_hosts: str = ""  # lista separada por comas; vacío = cualquier host (solo admin/revisor pueden iniciar)
    # dónde corre la GPU: local = subprocesos de esta máquina · remote = solo workers registrados
    # · auto = en un worker si hay uno en línea y, si no, aquí
    gpu_mode: str = "local"
    worker_offline_after: float = 60.0  # segundos sin latidos para dar a un worker por desconectado
    worker_queue_timeout: float = (
        120.0  # cuánto espera un trabajo interactivo (detectar imágenes) a que un worker lo tome
    )
    workspace_ports: str = "9100-9199"  # puertos SSH fijos de los espacios de trabajo (uno por proyecto)
    session_cookie: str = "labelhub_session"
    session_max_age: int = 60 * 60 * 24 * 14


settings = Settings()
