/** @odoo-module **/

import { Component, onMounted, onPatched, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";

const OPEN = ["not in", ["delivered", "cancel"]];

// One colour per item stage, reused by the doughnut and the stage legend so the
// dashboard reads as one picture.
const STAGE_COLOURS = {
    artwork_pending: "#f0ad4e",
    awaiting_approval: "#d9534f",
    approved: "#6f42c1",
    in_production: "#0d6efd",
    qc: "#17a2b8",
    ready: "#28a745",
};

export class PrintingDashboard extends Component {
    static template = "kma_printing_job.PrintingDashboard";
    static props = ["*"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ loading: true, days: 30, data: null });
        this.stageCanvas = useRef("stageChart");
        this.trendCanvas = useRef("trendChart");
        this.charts = [];

        onWillStart(async () => {
            await loadBundle("web.chartjs_lib");
            await this.load();
        });
        onMounted(() => this.drawCharts());
        onPatched(() => this.drawCharts());
        onWillUnmount(() => this.destroyCharts());
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(
                "kma.printing.job", "get_printing_dashboard", [this.state.days]
            );
        } finally {
            this.state.loading = false;
        }
    }

    async setPeriod(days) {
        this.state.days = days;
        await this.load();
    }

    get data() {
        return this.state.data;
    }

    // ------------------------------------------------------------- charts
    destroyCharts() {
        this.charts.forEach((chart) => chart.destroy());
        this.charts = [];
    }

    drawCharts() {
        this.destroyCharts();
        if (this.state.loading || !this.state.data) {
            return;
        }
        const legend = {
            position: "right",
            labels: { boxWidth: 12, usePointStyle: true, pointStyle: "circle", padding: 12 },
        };
        const mix = this.state.data.stage_mix;
        if (this.stageCanvas.el && mix.length) {
            this.charts.push(new Chart(this.stageCanvas.el, {
                type: "doughnut",
                data: {
                    labels: mix.map((row) => row.label),
                    datasets: [{
                        data: mix.map((row) => row.count),
                        backgroundColor: mix.map((row) => STAGE_COLOURS[row.key] || "#adb5bd"),
                        borderWidth: 0,
                    }],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: "62%",
                    plugins: { legend },
                },
            }));
        }
        const trend = this.state.data.trend;
        if (this.trendCanvas.el && trend.length) {
            this.charts.push(new Chart(this.trendCanvas.el, {
                type: "bar",
                data: {
                    labels: trend.map((row) => row.label),
                    datasets: [
                        {
                            label: "Jobs created",
                            data: trend.map((row) => row.created),
                            backgroundColor: "#7C7BAD",
                            borderRadius: 4,
                        },
                        {
                            label: "Delivered",
                            data: trend.map((row) => row.delivered),
                            backgroundColor: "#28a745",
                            borderRadius: 4,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: { grid: { display: false } },
                        y: { beginAtZero: true, ticks: { precision: 0 } },
                    },
                    plugins: {
                        legend: { position: "top", labels: { boxWidth: 12, usePointStyle: true, pointStyle: "circle" } },
                    },
                },
            }));
        }
    }

    // ------------------------------------------------------- formatting
    amount(value) {
        const kpi = this.state.data.kpi;
        const text = (value || 0).toLocaleString(undefined, {
            minimumFractionDigits: 2, maximumFractionDigits: 2,
        });
        return kpi.position === "before" ? `${kpi.currency} ${text}` : `${text} ${kpi.currency}`;
    }

    // ---------------------------------------------------------- drilldown
    openItems(name, domain) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "kma.printing.job.line",
            views: [[false, "list"], [false, "form"]],
            domain,
            target: "current",
        });
    }

    openJobs(name, domain) {
        this.action.doAction({
            type: "ir.actions.act_window",
            name,
            res_model: "kma.printing.job",
            views: [[false, "list"], [false, "form"]],
            domain,
            target: "current",
        });
    }

    openItemsInStage(stage, label) {
        this.openItems(label, [["stage", "=", stage]]);
    }

    openOpenJobs() {
        this.openJobs("Open Printing Jobs", [["stage", ...OPEN]]);
    }

    openOpenItems() {
        this.openItems("Open Printing Items", [["stage", ...OPEN]]);
    }

    openDeadline(stateKey, label) {
        this.openJobs(label, [["stage", ...OPEN], ["deadline_state", "=", stateKey]]);
    }

    openItem(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "kma.printing.job.line",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }

    openJob(id) {
        this.action.doAction({
            type: "ir.actions.act_window",
            res_model: "kma.printing.job",
            res_id: id,
            views: [[false, "form"]],
            target: "current",
        });
    }
}

registry.category("actions").add("kma_printing_dashboard", PrintingDashboard);
