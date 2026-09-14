"""Initialisation unique et explicite du registre ; jamais dans la commande de démarrage."""
import asyncio
import os
import sys

from utils.evo_budget import initialize_postgres, initialize_sqlite
from utils.evo_config import EvoError


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage : python -m scripts.evo_init_budget --postgres OU /chemin/evo.sqlite3")
    if sys.argv[1] == "--postgres":
        dsn = (os.getenv("EVO_DATABASE_URL") or os.getenv("DATABASE_URL") or "").strip()
        if not dsn.startswith(("postgres://", "postgresql://")):
            raise SystemExit("Configure EVO_DATABASE_URL avec le DSN PostgreSQL, dans l'environnement.")
        try:
            asyncio.run(initialize_postgres(dsn))
        except EvoError as exc:
            raise SystemExit(str(exc)) from None
        except Exception:
            raise SystemExit("Initialisation PostgreSQL impossible. Vérifie la connexion et les droits ; aucun secret n'est affiché.") from None
    else:
        if os.getenv("RENDER") or os.getenv("RENDER_SERVICE_ID"):
            raise SystemExit("Sur Render, utilise PostgreSQL, pas un disque éphémère.")
        try:
            initialize_sqlite(sys.argv[1])
        except FileExistsError:
            raise SystemExit("Ce fichier existe déjà : aucune remise à zéro autorisée.") from None
    print("Registre initialisé. Retire cette commande d'initialisation du build et conserve ce stockage.")


if __name__ == "__main__":
    main()
