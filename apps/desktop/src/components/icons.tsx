import {
  createContext,
  forwardRef,
  useContext,
  type ForwardRefExoticComponent,
  type RefAttributes,
} from "react";
import { HugeiconsIcon, type HugeiconsProps, type IconSvgElement } from "@hugeicons/react";
import * as Hugeicons from "./iconData";

export type NtrpIconProps = HugeiconsProps & {
  mirrored?: boolean;
};

export type NtrpIcon = ForwardRefExoticComponent<
  Omit<NtrpIconProps, "ref"> & RefAttributes<SVGSVGElement>
>;

type IconDefaults = Pick<NtrpIconProps, "color" | "size" | "strokeWidth">;

export const IconContext = createContext<IconDefaults>({
  color: "currentColor",
  strokeWidth: 1.5,
});

function defineIcon(icon: IconSvgElement, displayName: string): NtrpIcon {
  const Component = forwardRef<SVGSVGElement, NtrpIconProps>(
    ({ color, size, strokeWidth, mirrored, style, ...props }, ref) => {
      const defaults = useContext(IconContext);
      return (
        <HugeiconsIcon
          ref={ref}
          icon={icon}
          color={color ?? defaults.color}
          size={size ?? defaults.size}
          strokeWidth={strokeWidth ?? defaults.strokeWidth}
          style={mirrored ? { ...style, transform: `${style?.transform ?? ""} scaleX(-1)`.trim() } : style}
          {...props}
        />
      );
    },
  );
  Component.displayName = displayName;
  return Component;
}

export const AlertCircle = defineIcon(Hugeicons.AlertCircle, "AlertCircle");
export const Archive = defineIcon(Hugeicons.Archive, "Archive");
export const ArchiveRestore = defineIcon(Hugeicons.ArchiveRestore, "ArchiveRestore");
export const ArrowLeft = defineIcon(Hugeicons.ArrowLeft, "ArrowLeft");
export const ArrowRight = defineIcon(Hugeicons.ArrowRight, "ArrowRight");
export const ArrowUp = defineIcon(Hugeicons.ArrowUp, "ArrowUp");
export const ArrowUpDown = defineIcon(Hugeicons.ArrowUpDown, "ArrowUpDown");
export const ArrowUpRight = defineIcon(Hugeicons.ArrowUpRight, "ArrowUpRight");
export const Bell = defineIcon(Hugeicons.Bell, "Bell");
export const Bot = defineIcon(Hugeicons.Bot, "Bot");
export const Box = defineIcon(Hugeicons.Box, "Box");
export const Boxes = defineIcon(Hugeicons.Boxes, "Boxes");
export const Brain = defineIcon(Hugeicons.Brain, "Brain");
export const Briefcase = defineIcon(Hugeicons.Briefcase, "Briefcase");
export const Cable = defineIcon(Hugeicons.Cable, "Cable");
export const Calendar = defineIcon(Hugeicons.Calendar, "Calendar");
export const CalendarClock = defineIcon(Hugeicons.CalendarClock, "CalendarClock");
export const CalendarDays = defineIcon(Hugeicons.CalendarDays, "CalendarDays");
export const Camera = defineIcon(Hugeicons.Camera, "Camera");
export const Check = defineIcon(Hugeicons.Check, "Check");
export const CheckCircle = defineIcon(Hugeicons.CheckCircle, "CheckCircle");
export const CheckCircle2 = defineIcon(Hugeicons.CheckmarkCircle02Icon, "CheckCircle2");
export const ChevronDown = defineIcon(Hugeicons.ChevronDown, "ChevronDown");
export const ChevronLeft = defineIcon(Hugeicons.ChevronLeft, "ChevronLeft");
export const ChevronRight = defineIcon(Hugeicons.ChevronRight, "ChevronRight");
export const ChevronUp = defineIcon(Hugeicons.ChevronUp, "ChevronUp");
export const Circle = defineIcon(Hugeicons.Circle, "Circle");
export const CircleDashed = defineIcon(Hugeicons.CircleDashedIcon, "CircleDashed");
export const CircleDot = defineIcon(Hugeicons.CircleDotIcon, "CircleDot");
export const Clock = defineIcon(Hugeicons.Clock, "Clock");
export const Code2 = defineIcon(Hugeicons.CodeIcon, "Code2");
export const Copy = defineIcon(Hugeicons.Copy, "Copy");
export const CornerDownLeft = defineIcon(Hugeicons.CornerDownLeft, "CornerDownLeft");
export const CornerUpLeft = defineIcon(Hugeicons.CornerUpLeft, "CornerUpLeft");
export const Database = defineIcon(Hugeicons.Database, "Database");
export const DollarSign = defineIcon(Hugeicons.DollarSign, "DollarSign");
export const Dot = defineIcon(Hugeicons.DotIcon, "Dot");
export const Edit3 = defineIcon(Hugeicons.PencilEdit02Icon, "Edit3");
export const Eraser = defineIcon(Hugeicons.Eraser, "Eraser");
export const ExternalLink = defineIcon(Hugeicons.ExternalLink, "ExternalLink");
export const Eye = defineIcon(Hugeicons.Eye, "Eye");
export const FilePlus2 = defineIcon(Hugeicons.FileAddIcon, "FilePlus2");
export const FileSearch = defineIcon(Hugeicons.FileSearch, "FileSearch");
export const FileText = defineIcon(Hugeicons.FileText, "FileText");
export const Folder = defineIcon(Hugeicons.Folder, "Folder");
export const FolderInput = defineIcon(Hugeicons.FolderImportIcon, "FolderInput");
export const FolderOpen = defineIcon(Hugeicons.FolderOpen, "FolderOpen");
export const FolderPlus = defineIcon(Hugeicons.FolderPlus, "FolderPlus");
export const GitBranch = defineIcon(Hugeicons.GitBranch, "GitBranch");
export const GitPullRequest = defineIcon(Hugeicons.GitPullRequest, "GitPullRequest");
export const Globe = defineIcon(Hugeicons.Globe, "Globe");
export const Hash = defineIcon(Hugeicons.Hash, "Hash");
export const HelpCircle = defineIcon(Hugeicons.HelpCircleIcon, "HelpCircle");
export const History = defineIcon(Hugeicons.History, "History");
export const House = defineIcon(Hugeicons.House, "House");
export const Image = defineIcon(Hugeicons.Image, "Image");
export const ImagePlus = defineIcon(Hugeicons.ImagePlus, "ImagePlus");
export const Inbox = defineIcon(Hugeicons.Inbox, "Inbox");
export const KeyRound = defineIcon(Hugeicons.KeyRound, "KeyRound");
export const Keyboard = defineIcon(Hugeicons.Keyboard, "Keyboard");
export const Layers = defineIcon(Hugeicons.Layers, "Layers");
export const Library = defineIcon(Hugeicons.Library, "Library");
export const Link2 = defineIcon(Hugeicons.Link2, "Link2");
export const List = defineIcon(Hugeicons.List, "List");
export const ListChecks = defineIcon(Hugeicons.ListChecks, "ListChecks");
export const Loader2 = defineIcon(Hugeicons.Loading03Icon, "Loader2");
export const Mail = defineIcon(Hugeicons.Mail, "Mail");
export const Maximize2 = defineIcon(Hugeicons.Maximize2, "Maximize2");
export const MessageCircle = defineIcon(Hugeicons.MessageCircle, "MessageCircle");
export const MessageSquare = defineIcon(Hugeicons.MessageSquare, "MessageSquare");
export const MessageSquareText = defineIcon(Hugeicons.MessageSquareText, "MessageSquareText");
export const Minimize2 = defineIcon(Hugeicons.Minimize2, "Minimize2");
export const Minus = defineIcon(Hugeicons.Minus, "Minus");
export const Monitor = defineIcon(Hugeicons.Monitor, "Monitor");
export const Moon = defineIcon(Hugeicons.Moon, "Moon");
export const MoreHorizontal = defineIcon(Hugeicons.MoreHorizontal, "MoreHorizontal");
export const NotebookText = defineIcon(Hugeicons.NotebookText, "NotebookText");
export const Palette = defineIcon(Hugeicons.Palette, "Palette");
export const PanelLeft = defineIcon(Hugeicons.PanelLeft, "PanelLeft");
export const PanelLeftClose = defineIcon(Hugeicons.PanelLeftClose, "PanelLeftClose");
export const PanelLeftOpen = defineIcon(Hugeicons.PanelLeftOpen, "PanelLeftOpen");
export const PanelRightClose = defineIcon(Hugeicons.PanelRightClose, "PanelRightClose");
export const PanelRightOpen = defineIcon(Hugeicons.PanelRightOpen, "PanelRightOpen");
export const Pause = defineIcon(Hugeicons.Pause, "Pause");
export const PenLine = defineIcon(Hugeicons.PenLine, "PenLine");
export const Pencil = defineIcon(Hugeicons.Pencil, "Pencil");
export const Pin = defineIcon(Hugeicons.Pin, "Pin");
export const PinOff = defineIcon(Hugeicons.PinOff, "PinOff");
export const Play = defineIcon(Hugeicons.Play, "Play");
export const Plug = defineIcon(Hugeicons.Plug, "Plug");
export const Plus = defineIcon(Hugeicons.Plus, "Plus");
export const Power = defineIcon(Hugeicons.Power, "Power");
export const Prohibit = defineIcon(Hugeicons.BanIcon, "Prohibit");
export const Question = defineIcon(Hugeicons.Question, "Question");
export const Radio = defineIcon(Hugeicons.Radio, "Radio");
export const RefreshCw = defineIcon(Hugeicons.RefreshCw, "RefreshCw");
export const Repeat2 = defineIcon(Hugeicons.Repeat2, "Repeat2");
export const RotateCcw = defineIcon(Hugeicons.RotateCcw, "RotateCcw");
export const RotateCw = defineIcon(Hugeicons.RotateCw, "RotateCw");
export const Search = defineIcon(Hugeicons.Search, "Search");
export const SendHorizontal = defineIcon(Hugeicons.SendHorizontal, "SendHorizontal");
export const Settings = defineIcon(Hugeicons.Settings, "Settings");
export const Settings2 = defineIcon(Hugeicons.Settings2, "Settings2");
export const ShieldCheck = defineIcon(Hugeicons.ShieldCheck, "ShieldCheck");
export const ShieldOff = defineIcon(Hugeicons.ShieldBanIcon, "ShieldOff");
export const Slash = defineIcon(Hugeicons.CircleSlash2Icon, "Slash");
export const SlidersHorizontal = defineIcon(Hugeicons.SlidersHorizontal, "SlidersHorizontal");
export const Sparkles = defineIcon(Hugeicons.Sparkles, "Sparkles");
export const Split = defineIcon(Hugeicons.Split, "Split");
export const Square = defineIcon(Hugeicons.Square, "Square");
export const SquareCheck = defineIcon(Hugeicons.CheckmarkSquare02Icon, "SquareCheck");
export const SquareTerminal = defineIcon(Hugeicons.ComputerTerminal01Icon, "SquareTerminal");
export const Stop = defineIcon(Hugeicons.StopIcon, "Stop");
export const Sun = defineIcon(Hugeicons.Sun, "Sun");
export const Tag = defineIcon(Hugeicons.Tag, "Tag");
export const Target = defineIcon(Hugeicons.Target, "Target");
export const Terminal = defineIcon(Hugeicons.Terminal, "Terminal");
export const Text = defineIcon(Hugeicons.Text, "Text");
export const Trash2 = defineIcon(Hugeicons.Trash2, "Trash2");
export const TriangleAlert = defineIcon(Hugeicons.Alert02Icon, "TriangleAlert");
export const Workflow = defineIcon(Hugeicons.Workflow, "Workflow");
export const Wrench = defineIcon(Hugeicons.Wrench, "Wrench");
export const X = defineIcon(Hugeicons.X, "X");
export const Zap = defineIcon(Hugeicons.Zap, "Zap");
