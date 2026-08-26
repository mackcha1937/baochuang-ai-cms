import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { apiGet, AppError } from "../lib/api";
import type { BootstrapData } from "../types";

interface AppContextValue {
  data: BootstrapData | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
}

const AppContext = createContext<AppContextValue | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<BootstrapData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await apiGet<BootstrapData>("/bootstrap"));
    } catch (reason) {
      setError(reason instanceof AppError ? reason.message : "启动数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const value = useMemo(() => ({ data, loading, error, reload }), [data, error, loading, reload]);
  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useAppContext(): AppContextValue {
  const context = useContext(AppContext);
  if (!context) throw new Error("useAppContext must be used inside AppProvider");
  return context;
}
