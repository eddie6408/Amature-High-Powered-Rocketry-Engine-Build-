import { useEffect, useState } from "react";

/** Minimal hash router: #/page/arg1/arg2 */
export function useRoute(): string[] {
  const parse = () => window.location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  const [route, setRoute] = useState(parse);
  useEffect(() => {
    const on = () => setRoute(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
}

export const go = (...parts: string[]) => {
  window.location.hash = "/" + parts.map(encodeURIComponent).join("/");
};
