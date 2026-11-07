"""Bounded, round-robin download scheduling across independently paced sites."""
from __future__ import annotations

import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

from src.downloader import SOURCE_HOSTS


def run_parallel(crawler, mode, counters):
    sources = list(SOURCE_HOSTS)
    pending = {}
    busy = set()
    empty = set()
    cursor = 0
    submitted = completed = 0
    last_idle_poll = time.monotonic()
    limit = crawler.config.max_documents
    pool = ThreadPoolExecutor(max_workers=crawler.config.download_workers, thread_name_prefix="download")
    print(f"[crawler] Загрузчиков: {crawler.config.download_workers}; разбор: {crawler.config.parse_mode}")
    try:
        while True:
            crawler.disk_guard.check()
            # Consume every finished future before allocating any further work.
            for future in list(pending):
                if not future.done():
                    continue
                task = pending.pop(future)
                busy.remove(task["source"])
                result = future.result()  # unexpected failures stop, never fake a successful task
                counters[result] += 1
                completed += 1
                print(f"[{completed}] {task['source']:13s} {result:9s} {task['url']}", flush=True)
            if limit is not None and submitted >= limit and not pending:
                break

            now = int(crawler.now())
            # Discovery is a barrier: never refresh rules/inventory concurrently
            # with article requests. It uses the same global pacing gate.
            refresh_due = (mode == "normal" and crawler.config.run_forever and
                           crawler.config.refresh_sitemaps and crawler.next_sitemap_refresh_at is not None and
                           now >= crawler.next_sitemap_refresh_at)
            if refresh_due and not pending:
                print(f"[crawler] Очередь пополнена: {crawler.update_frontier()} URL")
                empty.clear()
                now = int(crawler.now())
                refresh_due = False

            waits = []
            if not refresh_due:
                for _ in sources:
                    if len(pending) >= crawler.config.download_workers or (limit is not None and submitted >= limit):
                        break
                    source = sources[cursor]
                    cursor = (cursor + 1) % len(sources)
                    if source in busy or source in empty or crawler.source_cooldowns.get(source, 0) > now:
                        continue
                    delay = crawler.gate.ready_in(source)
                    if delay > 0:
                        waits.append(delay)
                        continue
                    task = crawler.claim(now, mode=mode, source=source)
                    if task is None:
                        empty.add(source)
                        continue
                    busy.add(source)
                    submitted += 1
                    pending[pool.submit(crawler.process, task)] = task

            if pending:
                wait(pending, timeout=min([0.25, *waits]), return_when=FIRST_COMPLETED)
                continue
            if waits:
                # No live HTTP task: sleep until a local site becomes ready.
                crawler.gate._pause(min(waits))
                continue
            if not crawler.config.run_forever:
                break
            # Retry future URL deadlines and persisted source cooldowns at the
            # configured polling interval, not in a tight loop over MongoDB.
            interval = crawler.config.poll_seconds
            if crawler.next_sitemap_refresh_at is not None and mode == "normal" and crawler.config.refresh_sitemaps:
                interval = min(interval, max(0.01, crawler.next_sitemap_refresh_at - crawler.now()))
            elapsed = time.monotonic() - last_idle_poll
            crawler.gate._pause(max(0.01, interval - min(elapsed, interval)))
            empty.clear()
            last_idle_poll = time.monotonic()
    finally:
        # Ctrl+C/fatal DB error: interrupt rate-limit waits, cancel queued work,
        # finish bounded outstanding HTTP before releasing the coordinator lock.
        # Unfinished leases are recovered by prepare() on the next launch.
        crawler.gate.stop.set()
        pool.shutdown(wait=True, cancel_futures=True)
    print(f"[crawler] {counters}")
    return counters
