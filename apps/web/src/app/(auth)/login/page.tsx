"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState, type FormEvent } from "react";

export default function LoginPage() {
  // Next.js 16: useSearchParams() must live inside a Suspense boundary.
  return (
    <main className="flex min-h-full items-center justify-center px-6 py-16">
      <Suspense fallback={null}>
        <LoginForm />
      </Suspense>
    </main>
  );
}

function LoginForm() {
  const router = useRouter();
  const sp = useSearchParams();
  const next = sp.get("next") ?? "/chat";

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setPending(true);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const json = await res.json().catch(() => null);
        setError(
          json?.detail === "Invalid email or password"
            ? "이메일 또는 비밀번호가 올바르지 않습니다."
            : (json?.detail ?? `로그인 실패 (${res.status})`),
        );
        return;
      }
      router.replace(next);
      router.refresh();
    } catch {
      setError("서버에 연결할 수 없습니다.");
    } finally {
      setPending(false);
    }
  }

  return (
    <div className="w-full max-w-sm rounded-lg border border-border-default bg-bg-canvas p-8 shadow-sm">
      <header className="mb-6 space-y-1">
        <span className="inline-block rounded-pill bg-accent-subtle px-3 py-1 text-xs font-medium text-accent-emphasis">
          axiomedge
        </span>
        <h1 className="text-2xl font-semibold leading-snug text-fg-default">
          로그인
        </h1>
        <p className="text-sm text-fg-muted">
          등록된 계정으로 로그인하세요.
        </p>
      </header>

      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        <label className="block text-xs font-medium text-fg-muted">
          이메일
          <input
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 block h-9 w-full rounded-md border border-border-default bg-bg-canvas px-3 text-sm text-fg-default placeholder:text-fg-subtle focus:border-accent-default focus:outline-none"
            placeholder="you@example.com"
          />
        </label>

        <label className="block text-xs font-medium text-fg-muted">
          비밀번호
          <input
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 block h-9 w-full rounded-md border border-border-default bg-bg-canvas px-3 text-sm text-fg-default placeholder:text-fg-subtle focus:border-accent-default focus:outline-none"
            placeholder="********"
          />
        </label>

        {error && (
          <p
            role="alert"
            className="rounded-md bg-danger-subtle px-3 py-2 text-xs text-danger-default"
          >
            {error}
          </p>
        )}

        <button
          type="submit"
          disabled={pending}
          className="h-9 w-full rounded-md bg-accent-default px-4 text-sm font-medium text-fg-onAccent transition-colors hover:bg-accent-emphasis disabled:opacity-50"
        >
          {pending ? "로그인 중..." : "로그인"}
        </button>
      </form>
    </div>
  );
}
