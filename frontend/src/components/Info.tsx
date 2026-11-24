"use client";

import { useId, useState } from "react";
import { GLOSSARY } from "@/lib/glossary";
import { Icon } from "./Icons";

/**
 * A small "?" that explains a term in plain English.
 *
 * Opens on hover, keyboard focus, or click (so it works on touch screens), and
 * closes on Escape or when focus leaves.
 */
export function Info({
  term,
  title,
  text,
  align = "left",
}: {
  term?: keyof typeof GLOSSARY;
  title?: string;
  text?: string;
  align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const entry = term ? GLOSSARY[term] : undefined;
  const heading = title ?? entry?.title;
  const body = text ?? entry?.body;
  if (!body) return null;

  return (
    <span
      className="relative inline-flex align-middle"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-label={`What is ${heading ?? "this"}?`}
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
        className="rounded-full text-mist-500 transition-colors hover:text-accent"
      >
        <Icon name="info" className="h-[15px] w-[15px]" />
      </button>
      {open && (
        <span
          id={id}
          role="tooltip"
          className={`absolute top-full z-40 mt-2 w-64 animate-fade-in rounded-xl bg-ink-900 p-3.5 text-left shadow-float ${
            align === "right" ? "right-0" : "-left-2"
          }`}
        >
          {heading && (
            <span className="mb-1 block font-display text-xs font-semibold text-mist-100">
              {heading}
            </span>
          )}
          <span className="block text-2xs font-normal leading-relaxed text-mist-300">
            {body}
          </span>
        </span>
      )}
    </span>
  );
}
