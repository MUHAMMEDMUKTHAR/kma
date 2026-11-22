/** @odoo-module **/

// A proof decision must reach us exactly once. Double-clicking "Approve & Print"
// would otherwise post twice, and the second post hits an item that has already
// moved on. The pressed value is copied into a hidden input first: disabling a
// submit button removes its name/value from the POST body.
function guardDecisionForms() {
    document.querySelectorAll("form.o_kma_decision").forEach((form) => {
        let pressed = null;
        form.querySelectorAll('button[type="submit"]').forEach((button) => {
            button.addEventListener("click", () => {
                pressed = button;
            });
        });

        form.addEventListener("submit", (ev) => {
            if (form.dataset.kmaSubmitted) {
                ev.preventDefault();
                return;
            }
            form.dataset.kmaSubmitted = "1";

            const chosen = ev.submitter || pressed;
            let carried = form.querySelector('input[type="hidden"][name="decision"]');
            if (!carried) {
                carried = document.createElement("input");
                carried.type = "hidden";
                carried.name = "decision";
                form.appendChild(carried);
            }
            carried.value = chosen ? chosen.value : "approve";

            form.querySelectorAll('button[type="submit"]').forEach((button) => {
                button.disabled = true;
                if (button === chosen) {
                    button.innerHTML =
                        '<i class="fa fa-circle-o-notch fa-spin me-1" aria-hidden="true"></i>Sending…';
                }
            });
        });
    });
}

if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", guardDecisionForms);
} else {
    guardDecisionForms();
}
