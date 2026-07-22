(() => {
  const queued = [];
  let sonner = null;

  const normalize = (input, options = {}) =>
    typeof input === "string"
      ? { title: input, ...options }
      : { ...input, ...options };

  window.BOARD_TOAST = Object.freeze({
    show(input, options) {
      const payload = normalize(input, options);
      if (sonner) return sonner.show(payload);
      queued.push(payload);
      return true;
    },
    dismiss(id) {
      if (!sonner) return false;
      sonner.dismiss(id);
      return true;
    },
  });

  const root = document.createElement("div");
  root.className = "dp-sonner-root";
  document.body.append(root);

  Promise.all([
    import("https://esm.sh/react@19.2.0"),
    import("https://esm.sh/react-dom@19.2.0/client?deps=react@19.2.0"),
    import("https://esm.sh/sonner@2?deps=react@19.2.0,react-dom@19.2.0"),
  ]).then(([ReactModule, ReactDOMModule, SonnerModule]) => {
    const React = ReactModule.default;
    const h = React.createElement;
    const { createRoot } = ReactDOMModule;
    const { Toaster, toast } = SonnerModule;

    const renderToast = (payload) =>
      h(
        "div",
        { className: "dp-sonner-card", "data-tone": payload.tone || "neutral" },
        h(
          "div",
          { className: "dp-sonner-head" },
          h("strong", { className: "dp-sonner-title" }, payload.title),
          payload.status ? h("span", { className: "dp-sonner-status" }, payload.status) : null,
        ),
        payload.description
          ? h("p", { className: "dp-sonner-description" }, payload.description)
          : null,
      );

    sonner = {
      show(payload) {
        return toast.custom(() => renderToast(payload));
      },
      dismiss(id) {
        toast.dismiss(id);
      },
    };

    createRoot(root).render(
      h(Toaster, {
        position: "bottom-right",
        visibleToasts: 3,
        gap: 10,
        offset: 16,
        duration: window.BOARD_MOTION?.duration?.toastLifetime ?? 4000,
        toastOptions: { unstyled: true },
      }),
    );

    queued.splice(0).forEach((payload) => sonner.show(payload));
  });
})();
