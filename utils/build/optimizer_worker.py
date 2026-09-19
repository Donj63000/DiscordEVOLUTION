"""Un processus local interrompable, une recherche à la fois, pas de file infinie."""
import asyncio
import json
import multiprocessing
import queue
from .models import Build, Catalog, BuildError, canonical
from .rules import Rules
from .optimizer import Constraints, Limits, optimize


def _worker(output, build, catalog, rules, constraints, limits):
    try:
        result = optimize(Build.model_validate_json(build), Catalog.model_validate_json(catalog),
                          Rules.model_validate_json(rules), Constraints.model_validate_json(constraints),
                          Limits.model_validate_json(limits))
        raw = canonical(result)
        if len(raw.encode()) > 2 * 1024 * 1024:
            raw = canonical({"status": "RESOURCE_LIMIT", "solutions": [], "tentative": [], "message": "Résultat trop volumineux."})
        output.put(raw, timeout=2)
    except Exception:
        output.put(canonical({"status": "ERROR", "solutions": [], "tentative": [], "message": "Calcul interrompu sur données incompatibles."}), timeout=2)


class OptimizerWorker:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.process = None
        self.closed = False

    async def run(self, build, catalog, rules, constraints, limits=None):
        if self.closed:
            raise BuildError("Optimiseur fermé.")
        if self.lock.locked():
            raise BuildError("Une recherche est déjà en cours ; aucune nouvelle tâche n'a été mise en attente.")
        limits = limits or Limits()
        async with self.lock:
            ctx = multiprocessing.get_context("spawn")
            output = ctx.Queue(maxsize=1)
            process = ctx.Process(target=_worker, args=(output, canonical(build), canonical(catalog), canonical(rules), canonical(constraints), canonical(limits)), daemon=True)
            self.process = process
            try:
                process.start()
                async with asyncio.timeout(limits.seconds + 8):
                    while True:
                        try:
                            raw = output.get_nowait()
                            return json.loads(raw)
                        except queue.Empty:
                            if not process.is_alive():
                                # Le feeder peut finir juste après la sortie du worker.
                                await asyncio.sleep(0.1)
                                try:
                                    return json.loads(output.get_nowait())
                                except queue.Empty:
                                    raise BuildError("Worker d'optimisation arrêté sans résultat.") from None
                            await asyncio.sleep(0.05)
            except TimeoutError as exc:
                raise BuildError("Limite de calcul atteinte. Le processus de recherche a été arrêté.") from exc
            finally:
                if process.pid is not None:
                    if process.is_alive():
                        process.terminate()
                    await asyncio.to_thread(process.join, 2)
                    if process.is_alive():
                        process.kill()
                        await asyncio.to_thread(process.join, 2)
                output.cancel_join_thread()
                output.close()
                process.close()
                self.process = None

    async def close(self):
        self.closed = True
        process = self.process
        if process is not None and process.is_alive():
            process.terminate()
        # run() reste l'unique propriétaire de join/close : pas de course entre
        # deux nettoyages concurrents du même handle de processus.
        async with self.lock:
            pass
