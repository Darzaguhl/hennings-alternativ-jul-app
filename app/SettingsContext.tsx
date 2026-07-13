import AsyncStorage from "@react-native-async-storage/async-storage";
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

type TimeFormat = "12" | "24";
type DateFormat = "MDY" | "DMY";

interface SettingsContextValue {
  timeFormat: TimeFormat;
  dateFormat: DateFormat;
  setTimeFormat: (value: TimeFormat) => Promise<void>;
  setDateFormat: (value: DateFormat) => Promise<void>;
  loading: boolean;
}

const DEFAULT_TIME_FORMAT: TimeFormat = "12";
const DEFAULT_DATE_FORMAT: DateFormat = "MDY";

const STORAGE_KEYS = {
  time: "settings_time_format",
  date: "settings_date_format",
};

const SettingsContext = createContext<SettingsContextValue | undefined>(undefined);

export const SettingsProvider = ({ children }: { children: React.ReactNode }) => {
  const [timeFormat, setTimeFormatState] = useState<TimeFormat>(DEFAULT_TIME_FORMAT);
  const [dateFormat, setDateFormatState] = useState<DateFormat>(DEFAULT_DATE_FORMAT);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const loadSettings = async () => {
      try {
        const [[, storedTime], [, storedDate]] = await AsyncStorage.multiGet([
          STORAGE_KEYS.time,
          STORAGE_KEYS.date,
        ]);

        if (storedTime === "12" || storedTime === "24") {
          setTimeFormatState(storedTime);
        }

        if (storedDate === "MDY" || storedDate === "DMY") {
          setDateFormatState(storedDate);
        }
      } finally {
        setLoading(false);
      }
    };

    loadSettings();
  }, []);

  const setTimeFormat = useCallback(async (value: TimeFormat) => {
    setTimeFormatState(value);
    await AsyncStorage.setItem(STORAGE_KEYS.time, value);
  }, []);

  const setDateFormat = useCallback(async (value: DateFormat) => {
    setDateFormatState(value);
    await AsyncStorage.setItem(STORAGE_KEYS.date, value);
  }, []);

  return (
    <SettingsContext.Provider
      value={{ timeFormat, dateFormat, setTimeFormat, setDateFormat, loading }}
    >
      {children}
    </SettingsContext.Provider>
  );
};

export const useSettings = () => {
  const ctx = useContext(SettingsContext);
  if (!ctx) {
    throw new Error("useSettings must be used within SettingsProvider");
  }
  return ctx;
};
