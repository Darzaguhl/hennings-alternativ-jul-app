// app/AuthContext.tsx
import AsyncStorage from "@react-native-async-storage/async-storage";
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

const ACCESS_TOKEN_KEY = "accessToken";
const REFRESH_TOKEN_KEY = "refreshToken";
const API_BASE_URL = "https://hennings-alternativ-jul-api-preprod.onrender.com";

type FetchArgs = Parameters<typeof fetch>;

interface AuthUser {
  id: number;
  username: string;
  email: string;
}

interface AuthContextType {
  token: string | null;
  refreshToken: string | null;
  currentUser: AuthUser | null;
  loading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  apiFetch: (...args: FetchArgs) => ReturnType<typeof fetch>;
}

const noop = async () => {
  throw new Error("Auth context not initialised");
};

const AuthContext = createContext<AuthContextType>({
  token: null,
  refreshToken: null,
  currentUser: null,
  loading: true,
  login: noop,
  logout: noop,
  apiFetch: () => Promise.reject(new Error("Auth context not initialised")),
});

const resolveUrl = (input: FetchArgs[0]) => {
  if (typeof input === "string" && !/^https?:\/\//.test(input)) {
    const prefix = input.startsWith("/") ? "" : "/";
    return `${API_BASE_URL}${prefix}${input}`;
  }
  return input;
};

const buildHeaders = (headers?: HeadersInit) => {
  if (!headers) return new Headers();
  if (headers instanceof Headers) return new Headers(headers);
  return new Headers(headers);
};

export const AuthProvider = ({ children }: { children: React.ReactNode }) => {
  const [token, setToken] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState<string | null>(null);
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  // Prime auth state from storage on startup
  useEffect(() => {
    const loadTokens = async () => {
      try {
        const [[, storedAccess], [, storedRefresh]] = await AsyncStorage.multiGet([
          ACCESS_TOKEN_KEY,
          REFRESH_TOKEN_KEY,
        ]);

        setToken(storedAccess ?? null);
        setRefreshToken(storedRefresh ?? null);
      } catch (err) {
        console.error("Error loading auth tokens", err);
      } finally {
        setLoading(false);
      }
    };

    loadTokens();
  }, []);

  const fetchCurrentUser = useCallback(
    async (access?: string) => {
      const authToken = access ?? token;
      if (!authToken) return;
      try {
        const response = await fetch(`${API_BASE_URL}/api/users/me/`, {
          headers: { Authorization: `Bearer ${authToken}` },
        });
        if (response.ok) {
          const me: AuthUser = await response.json();
          setCurrentUser(me);
        }
      } catch (error) {
        console.error("Failed to load current user", error);
      }
    },
    [token]
  );

  useEffect(() => {
    if (!loading && token && !currentUser) {
      fetchCurrentUser();
    }
  }, [loading, token, currentUser, fetchCurrentUser]);

  const persistTokens = useCallback(async (access: string, refresh: string) => {
    await AsyncStorage.multiSet([
      [ACCESS_TOKEN_KEY, access],
      [REFRESH_TOKEN_KEY, refresh],
    ]);
    setToken(access);
    setRefreshToken(refresh);
  }, []);

  const clearTokens = useCallback(async () => {
    await AsyncStorage.multiRemove([ACCESS_TOKEN_KEY, REFRESH_TOKEN_KEY]);
    setToken(null);
    setRefreshToken(null);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    try {
      const response = await fetch(`${API_BASE_URL}/api/token/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });

      if (!response.ok) {
        const errorBody = await response.json().catch(() => ({}));
        const message = errorBody?.detail ?? "Invalid credentials";
        throw new Error(message);
      }

      const data: { access: string; refresh: string } = await response.json();
      if (!data.access || !data.refresh) {
        throw new Error("Malformed login response");
      }

      await persistTokens(data.access, data.refresh);

      await fetchCurrentUser(data.access);
    } catch (error) {
      console.error("Login failed", error);
      throw error;
    }
  }, [persistTokens, fetchCurrentUser]);

  const logout = useCallback(async () => {
    try {
      await clearTokens();
      setCurrentUser(null);
    } catch (error) {
      console.error("Error during logout", error);
      await clearTokens();
      setCurrentUser(null);
    }
  }, [clearTokens]);

  const apiFetch = useCallback(
    async (input: FetchArgs[0], init?: FetchArgs[1]) => {
      const url = resolveUrl(input);
      const initialHeaders = buildHeaders(init?.headers);

      if (token) {
        initialHeaders.set("Authorization", `Bearer ${token}`);
      }

      const finalInit: RequestInit = {
        ...init,
        headers: initialHeaders,
      };

      let response = await fetch(url, finalInit);

      if (response.status !== 401 || !refreshToken) {
        return response;
      }

      // Attempt to refresh the access token and retry once
      try {
        const refreshResponse = await fetch(`${API_BASE_URL}/api/token/refresh/`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh: refreshToken }),
        });

        if (!refreshResponse.ok) {
          await logout();
          return response;
        }

        const refreshData: { access?: string } = await refreshResponse.json();
        if (!refreshData.access) {
          await logout();
          return response;
        }

        await AsyncStorage.setItem(ACCESS_TOKEN_KEY, refreshData.access);
        setToken(refreshData.access);

        const retryHeaders = buildHeaders(init?.headers);
        retryHeaders.set("Authorization", `Bearer ${refreshData.access}`);

        response = await fetch(url, {
          ...init,
          headers: retryHeaders,
        });
      } catch (err) {
        console.error("Token refresh failed", err);
        await logout();
      }

      return response;
    },
    [token, refreshToken, logout]
  );

  const value = useMemo(
    () => ({ token, refreshToken, currentUser, loading, login, logout, apiFetch }),
    [token, refreshToken, currentUser, loading, login, logout, apiFetch]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export const useAuth = () => useContext(AuthContext);
