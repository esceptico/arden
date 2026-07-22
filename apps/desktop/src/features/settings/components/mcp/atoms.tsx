import { Plus, Trash2 } from "@/components/icons";
import { ICON } from "@/lib/icons";
import { Button } from "@/components/ui/Button";
import { IconButton } from "@/components/ui/IconButton";

export function AddBtn({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <Button variant="ghost" leadingIcon={Plus} onClick={onClick}>
      {label}
    </Button>
  );
}

export function RemoveBtn({ onClick }: { onClick: () => void }) {
  return (
    <IconButton size="lg" tone="faint" onClick={onClick} aria-label="Remove">
      <Trash2 size={ICON.SM} strokeWidth={2} />
    </IconButton>
  );
}
