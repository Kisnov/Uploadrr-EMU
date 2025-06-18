########################################################################################################
#####                    Credit to Audionut for the inspiration of this Cleanrr.                   #####
##### https://github.com/Audionut/Upload-Assistant/commit/0703aa899d82ad6a62025318559b5216270d43eaz ####
#####      Upon writing Uploadrr v2, I had an issue with graceful shut downs esp. w/ screens.      #####
########################################################################################################

import asyncio
import signal
import psutil
from src.console import log

running_subprocesses = set()

async def cleanrr(thread_executor=None):
    """
    Clean up running tasks, threads, and subprocesses.

    Args:
        thread_executor (ThreadPoolExecutor, optional): Executor to shut down.
    """
    log.debug("Starting cleanup process...")

    # Step 1: Shutdown ThreadPoolExecutor if active
    if thread_executor:
        log.debug("Shutting down thread pool executor...")
        thread_executor.shutdown(wait=True)

    # Step 2: Terminate tracked subprocesses
    for proc in list(running_subprocesses):
        if proc.returncode is None:
            log.debug(f"Attempting to terminate subprocess [pid={proc.pid}]...")
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3)
            except asyncio.TimeoutError:
                log.warning(f"Force killing subprocess [pid={proc.pid}]...")
                proc.kill()
                await proc.wait()
        running_subprocesses.discard(proc)
        for stream in (proc.stdout, proc.stderr, proc.stdin):
            if stream and not stream.closed:
                try:
                    if hasattr(stream, "aclose") and callable(stream.aclose):
                        await stream.aclose()
                    else:
                        stream.close()
                except Exception as e:
                    log.error(f"Error closing stream for pid={proc.pid}: {e}")

    # Step 3: Cancel all running asyncio tasks
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    if tasks:
        log.debug(f"Cancelling {len(tasks)} tasks...")
        for task in tasks:
            task.cancel()
        await asyncio.sleep(0.1)  # Give tasks a chance to cancel
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
                log.error(f"Unhandled exception during cleanup: {result}")

    # Step 4: Kill all child processes
    current_process = psutil.Process()
    children = current_process.children(recursive=True)
    for child in children:
        log.debug(f"Terminating child process [pid={child.pid}, name={child.name()}]...")
        child.terminate()
    _, still_alive = psutil.wait_procs(children, timeout=1)
    for child in still_alive:
        log.warning(f"Force killing stubborn process [pid={child.pid}]...")
        child.kill()

    log.debug("[green]Cleanup process completed.")

class CleanupHandler:
    """
    A class to handle cleanup and SIGINT signals for a specific context.
    
    Attributes:
        thread_executor (ThreadPoolExecutor, optional): Executor to manage.
        name (str): Identifier for logging purposes.
    """
    def __init__(self, thread_executor=None, name=""):
        self.thread_executor = thread_executor
        self._interrupted = False
        self._loop = asyncio.get_running_loop()
        self.name = name or "Unnamed"
        self._register_signal_handlers()

    def _register_signal_handlers(self):
        """Register SIGINT handler."""
        signal.signal(signal.SIGINT, self._handle_sigint)

    def _handle_sigint(self, sig, frame):
        """Handle SIGINT by marking interrupted and cancelling tasks."""
        log.debug(f"Received SIGINT in {self.name}, initiating cleanup...")
        self._interrupted = True
        for task in asyncio.all_tasks(self._loop):
            task.cancel()

    @property
    def interrupted(self):
        """Check if the handler has been interrupted."""
        return self._interrupted

    async def shutdown(self):
        """Perform cleanup for this handler's context."""
        log.debug(f"Shutting down {self.name}...")
        await cleanrr(self.thread_executor)