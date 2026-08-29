import {
  ArrowUpRight,
  Check,
  ChevronRight,
  CircleCheck,
  FileCheck2,
  LockKeyhole,
  Mic2,
  ShieldCheck,
} from "lucide-react";

const proofPoints = [
  "Direct beneficiary interviews",
  "Structured evidence capture",
  "Deterministic verification logic",
];

export default function Home() {
  return (
    <main className="relative min-h-screen overflow-hidden bg-background">
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_right,var(--grid-line)_1px,transparent_1px),linear-gradient(to_bottom,var(--grid-line)_1px,transparent_1px)] bg-[size:72px_72px] [mask-image:linear-gradient(to_bottom,black,transparent_82%)]" />
      <div className="pointer-events-none absolute -top-40 right-[-10rem] h-[32rem] w-[32rem] rounded-full bg-accent/10 blur-3xl" />

      <nav className="relative z-10 mx-auto flex w-full max-w-7xl items-center justify-between px-6 py-6 lg:px-10">
        <a href="#top" className="group flex items-center gap-3" aria-label="ProofCall home">
          <span className="flex size-10 items-center justify-center rounded-xl bg-foreground text-background shadow-lg shadow-foreground/10 transition-transform group-hover:-rotate-6">
            <ShieldCheck aria-hidden="true" className="size-5" />
          </span>
          <span className="font-display text-lg font-semibold tracking-tight">ProofCall</span>
        </a>
        <div className="hidden items-center gap-8 text-sm text-muted md:flex">
          <a className="transition-colors hover:text-foreground" href="#method">Method</a>
          <a className="transition-colors hover:text-foreground" href="#evidence">Evidence</a>
          <a className="transition-colors hover:text-foreground" href="#contact">Contact</a>
        </div>
        <a href="#contact" className="inline-flex items-center gap-2 rounded-full border border-border bg-card px-4 py-2 text-sm font-medium transition-all hover:border-accent hover:bg-accent hover:text-accent-foreground focus-visible:outline-none">
          Request a demo <ArrowUpRight aria-hidden="true" className="size-4" />
        </a>
      </nav>

      <section id="top" className="relative z-10 mx-auto grid w-full max-w-7xl gap-16 px-6 pb-24 pt-16 lg:grid-cols-[1.05fr_0.95fr] lg:items-center lg:px-10 lg:pb-36 lg:pt-24">
        <div className="max-w-3xl">
          <p className="mb-7 inline-flex items-center gap-2 rounded-full border border-accent/25 bg-accent/8 px-3 py-1.5 text-xs font-semibold uppercase tracking-[0.18em] text-accent">
            <span className="size-1.5 rounded-full bg-accent" /> Independent outcome verification
          </p>
          <h1 className="font-display text-5xl font-semibold leading-[0.98] tracking-[-0.055em] text-foreground sm:text-7xl lg:text-[5.5rem]">
            Proof over
            <span className="block text-accent">promises.</span>
          </h1>
          <p className="mt-8 max-w-xl text-lg leading-8 text-muted sm:text-xl">
            ProofCall gives programmes a defensible view of what happened after the report — grounded in conversations, evidence, and clear verification rules.
          </p>
          <div className="mt-10 flex flex-col gap-3 sm:flex-row">
            <a href="#contact" className="group inline-flex h-12 items-center justify-center gap-3 rounded-lg bg-accent px-6 text-sm font-semibold text-accent-foreground shadow-xl shadow-accent/15 transition-all hover:-translate-y-0.5 hover:bg-accent-hover hover:shadow-2xl focus-visible:outline-none">
              See how it works <ChevronRight aria-hidden="true" className="size-4 transition-transform group-hover:translate-x-1" />
            </a>
            <a href="#method" className="inline-flex h-12 items-center justify-center gap-2 rounded-lg border border-border bg-card px-6 text-sm font-semibold transition-colors hover:border-foreground/30 hover:bg-surface focus-visible:outline-none">
              Explore the method
            </a>
          </div>
          <div className="mt-12 flex flex-wrap gap-x-6 gap-y-3 text-sm text-muted">
            {proofPoints.map((point) => (
              <span key={point} className="inline-flex items-center gap-2"><CircleCheck aria-hidden="true" className="size-4 text-accent" />{point}</span>
            ))}
          </div>
        </div>

        <div id="evidence" className="relative mx-auto w-full max-w-md lg:ml-auto">
          <div className="absolute -inset-4 rounded-[2rem] border border-accent/15 bg-accent/5 rotate-3" />
          <div className="relative rounded-2xl border border-border bg-card p-5 shadow-2xl shadow-foreground/10 sm:p-7">
            <div className="flex items-center justify-between border-b border-border pb-5">
              <div className="flex items-center gap-3"><span className="flex size-10 items-center justify-center rounded-lg bg-accent/10 text-accent"><FileCheck2 className="size-5" /></span><div><p className="text-sm font-semibold">Verification case</p><p className="text-xs text-muted">PC-0428 · Beneficiary outcome</p></div></div>
              <span className="rounded-full bg-accent/10 px-2.5 py-1 text-xs font-semibold text-accent">Verified</span>
            </div>
            <div className="py-7"><p className="text-xs font-semibold uppercase tracking-[0.16em] text-muted">Reported outcome</p><p className="mt-2 font-display text-2xl font-semibold tracking-tight">Secured full-time employment</p><div className="mt-6 h-2 overflow-hidden rounded-full bg-surface"><div className="h-full w-[86%] rounded-full bg-accent" /></div><div className="mt-2 flex justify-between text-xs text-muted"><span>Evidence confidence</span><span className="font-semibold text-foreground">86%</span></div></div>
            <div className="grid gap-3 border-t border-border pt-5 sm:grid-cols-2"><div className="rounded-lg bg-surface p-3"><Mic2 className="size-4 text-accent" /><p className="mt-3 text-xs text-muted">Interview completed</p><p className="mt-1 text-sm font-semibold">12 min recording</p></div><div className="rounded-lg bg-surface p-3"><LockKeyhole className="size-4 text-accent" /><p className="mt-3 text-xs text-muted">Evidence secured</p><p className="mt-1 text-sm font-semibold">Audit-ready</p></div></div>
            <div className="mt-5 flex items-center gap-2 text-xs text-muted"><Check className="size-4 text-accent" /> Independent review trail attached</div>
          </div>
        </div>
      </section>

      <section id="method" className="relative z-10 border-t border-border bg-card/70 px-6 py-20 lg:px-10 lg:py-28"><div className="mx-auto grid max-w-7xl gap-12 lg:grid-cols-[0.7fr_1.3fr]"><div><p className="text-xs font-semibold uppercase tracking-[0.18em] text-accent">The method</p><h2 className="mt-4 max-w-sm font-display text-4xl font-semibold leading-tight tracking-[-0.04em]">A cleaner chain from claim to confidence.</h2></div><div className="grid gap-4 sm:grid-cols-3"><div className="border-l-2 border-accent pl-5"><p className="font-display text-xl font-semibold">01 / Listen</p><p className="mt-3 text-sm leading-6 text-muted">Speak directly with the people behind the reported result.</p></div><div className="border-l-2 border-border pl-5"><p className="font-display text-xl font-semibold">02 / Structure</p><p className="mt-3 text-sm leading-6 text-muted">Turn every conversation into consistent, reviewable evidence.</p></div><div className="border-l-2 border-border pl-5"><p className="font-display text-xl font-semibold">03 / Verify</p><p className="mt-3 text-sm leading-6 text-muted">Apply deterministic logic to reach a transparent conclusion.</p></div></div></div></section>

      <footer id="contact" className="relative z-10 mx-auto flex max-w-7xl flex-col gap-5 px-6 py-8 text-sm text-muted sm:flex-row sm:items-center sm:justify-between lg:px-10"><p>ProofCall / Independent verification infrastructure</p><a href="mailto:hello@proofcall.example" className="font-medium text-foreground transition-colors hover:text-accent">hello@proofcall.example <ArrowUpRight className="ml-1 inline size-4" /></a></footer>
    </main>
  );
}
