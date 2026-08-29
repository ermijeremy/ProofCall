import Image from "next/image";

export default function Home() {
  return (
    <div className="relative flex flex-1 items-center justify-center bg-background px-4 py-16 sm:px-6 lg:px-8">
      <div className="pointer-events-none absolute inset-0 overflow-hidden">
        <div
          className="absolute top-1/2 left-1/2 h-[600px] w-[600px] -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent/5 blur-3xl"
          style={{ willChange: "transform" }}
        />
      </div>

      <main className="relative z-10 flex flex-col items-center justify-between w-full max-w-2xl py-16 sm:py-24 lg:py-32">
        <div className="flex flex-col items-center gap-10 w-full">
          <div className="flex flex-col items-center gap-6">
            <div className="relative flex items-center justify-center">
              <div className="absolute inset-0 rounded-2xl bg-accent/10 blur-xl" />
              <div className="relative flex items-center justify-center w-14 h-14 rounded-xl bg-accent/5 border border-accent/20">
                <Image
                  src="/next.svg"
                  alt="ProofCall"
                  width={32}
                  height={32}
                  priority
                />
              </div>
            </div>

            <div className="flex flex-col items-center gap-4 text-center">
              <h1 className="text-4xl font-bold tracking-tight text-foreground sm:text-5xl lg:text-6xl leading-[1.1]">
                Verify with
                <span className="block text-accent">confidence</span>
              </h1>
              <p className="max-w-md text-lg leading-relaxed text-muted">
                Independently confirm employer-reported outcomes through
                direct beneficiary interviews, structured evidence, and
                deterministic verification logic.
              </p>
            </div>
          </div>

          <div className="flex flex-col gap-3 w-full sm:flex-row sm:items-center sm:justify-center">
            <a
              href="https://vercel.com/new?utm_source=create-next-app&utm_medium=appdir-template-tw&utm_campaign=create-next-app"
              target="_blank"
              rel="noopener noreferrer"
              className="group relative flex h-12 w-full items-center justify-center gap-2 rounded-lg bg-accent px-6 text-sm font-semibold text-accent-foreground shadow-md shadow-black/5 transition-all duration-200 hover:bg-accent-hover hover:shadow-lg hover:shadow-black/10 active:scale-[0.98] sm:w-auto"
            >
              <span className="absolute inset-0 rounded-lg bg-accent-foreground/10 opacity-0 transition-opacity duration-200 group-hover:opacity-100" />
              <Image
                className="relative z-10 h-4 w-4 dark:invert"
                src="/vercel.svg"
                alt="Vercel"
                width={16}
                height={14}
              />
              <span className="relative z-10">Get Started</span>
            </a>
            <a
              href="https://nextjs.org/docs?utm_source=create-next-app&utm_medium=appdir-template-tw&utm_campaign=create-next-app"
              target="_blank"
              rel="noopener noreferrer"
              className="group relative flex h-12 w-full items-center justify-center rounded-lg border border-border bg-card px-6 text-sm font-semibold text-foreground shadow-sm transition-all duration-200 hover:border-accent/30 hover:bg-accent/5 hover:shadow-md active:scale-[0.98] sm:w-auto"
            >
              <span className="absolute inset-0 rounded-lg bg-accent/5 opacity-0 transition-opacity duration-200 group-hover:opacity-100" />
              <span className="relative z-10">Documentation</span>
            </a>
          </div>
        </div>

        <div className="flex flex-col items-center gap-4 pt-8">
          <div className="flex items-center gap-2 text-xs text-muted">
            <div className="flex -space-x-1.5">
              <div className="h-5 w-5 rounded-full bg-accent/20 border-2 border-background" />
              <div className="h-5 w-5 rounded-full bg-accent/30 border-2 border-background" />
              <div className="h-5 w-5 rounded-full bg-accent/15 border-2 border-background" />
            </div>
            <span>Trusted verification for programmes</span>
          </div>
          <div className="flex items-center gap-6 text-xs text-muted">
            <a
              href="https://vercel.com/templates?framework=next.js&utm_source=create-next-app&utm_medium=appdir-template-tw&utm_campaign=create-next-app"
              className="transition-colors hover:text-foreground"
            >
              Templates
            </a>
            <a
              href="https://nextjs.org/learn?utm_source=create-next-app&utm_medium=appdir-template-tw&utm_campaign=create-next-app"
              className="transition-colors hover:text-foreground"
            >
              Learning
            </a>
          </div>
        </div>
      </main>
    </div>
  );
}