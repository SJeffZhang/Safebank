import { useEffect, useState, useCallback } from "react";
export function useLoad<T>(loader: () => Promise<T>, deps: unknown[] = []) {
  const [value, setValue] = useState<T>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [key, setKey] = useState(0);
  useEffect(() => {
    let live = true;
    setLoading(true);
    setError("");
    loader()
      .then((v) => {
        if (live) setValue(v);
      })
      .catch((e) => {
        if (live) setError(e instanceof Error ? e.message : "数据读取失败");
      })
      .finally(() => {
        if (live) setLoading(false);
      });
    return () => {
      live = false;
    };
  }, [...deps, key]);
  return {
    value,
    error,
    loading,
    reload: useCallback(() => setKey((k) => k + 1), []),
  };
}
