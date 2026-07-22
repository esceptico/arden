const LEGACY_STORAGE_PREFIX = "ntrp.";
const ARDEN_STORAGE_PREFIX = "arden.";

export function migrateLegacyStorage(storage: Storage = localStorage): number {
  const legacyKeys = Array.from({ length: storage.length }, (_, index) => storage.key(index)).filter(
    (key): key is string => key?.startsWith(LEGACY_STORAGE_PREFIX) ?? false,
  );
  let migrated = 0;

  for (const legacyKey of legacyKeys) {
    const ardenKey = `${ARDEN_STORAGE_PREFIX}${legacyKey.slice(LEGACY_STORAGE_PREFIX.length)}`;
    if (storage.getItem(ardenKey) === null) {
      const value = storage.getItem(legacyKey);
      if (value !== null) {
        storage.setItem(ardenKey, value);
        migrated += 1;
      }
    }
    storage.removeItem(legacyKey);
  }

  return migrated;
}

try {
  migrateLegacyStorage();
} catch {
  // Storage can be unavailable in hardened or embedded renderer contexts.
}
