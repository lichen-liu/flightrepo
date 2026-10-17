// Request activity is indeterminate: elapsed time is measured, progress is not guessed.
export class RequestActivity {
  constructor(container) {
    this.container = container;
    this.active = new Map();
    this.timer = null;
  }
  begin(label, priority = 0) {
    const token = Symbol(),
      started = performance.now();
    this.active.set(token, { label, priority, started });
    if (this.timer === null) this.timer = setInterval(() => this.render(), 250);
    this.render();
    return () => {
      this.active.delete(token);
      this.render();
    };
  }
  render() {
    const current = [...this.active.values()].sort(
      (a, b) => b.priority - a.priority || a.started - b.started,
    )[0];
    this.container.hidden = !current;
    if (!current) {
      clearInterval(this.timer);
      this.timer = null;
      return;
    }
    const seconds = Math.floor((performance.now() - current.started) / 1000);
    const set = (id, value) => {
      const node = this.container.querySelector(id);
      if (node.textContent !== value) node.textContent = value;
    };
    set("#request-label", current.label);
    set("#request-elapsed", `${seconds}s`);
    set(
      "#request-detail",
      seconds >= 8
        ? "Still waiting for a response. You can continue browsing."
        : "Waiting for a response.",
    );
  }
}
