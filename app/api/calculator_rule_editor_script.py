from __future__ import annotations


CALCULATOR_RULE_EDITOR_SCRIPT = r"""
<script>
(function () {
  "use strict";

  function one(root, selector) {
    return root.querySelector(selector);
  }

  function all(root, selector) {
    return Array.from(root.querySelectorAll(selector));
  }

  function safeParse(textarea, fallback) {
    try {
      var raw = String(textarea.value || "").trim();
      return raw ? JSON.parse(raw) : fallback;
    } catch (error) {
      return fallback;
    }
  }

  function writeJson(textarea, value) {
    textarea.value = JSON.stringify(value, null, 2);
    textarea.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function splitRefs(value) {
    return String(value || "")
      .split(",")
      .map(function (item) { return item.trim(); })
      .filter(Boolean);
  }

  function refsText(value) {
    return Array.isArray(value) ? value.join(", ") : "";
  }

  function clientTypesText(value) {
    if (value === "*") return "*";
    return Array.isArray(value) ? value.join(", ") : "*";
  }

  function parseClientTypes(value) {
    var raw = String(value || "").trim();
    return !raw || raw === "*" ? "*" : splitRefs(raw);
  }

  function boolOrNull(value) {
    if (value === "true") return true;
    if (value === "false") return false;
    return null;
  }

  function create(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function makeInput(label, value, options) {
    options = options || {};
    var wrapper = create("label", "guided-field");
    wrapper.appendChild(create("span", "", label));
    var input = document.createElement(options.type === "select" ? "select" : "input");
    if (options.type === "select") {
      (options.choices || []).forEach(function (choice) {
        var option = document.createElement("option");
        option.value = choice[0];
        option.textContent = choice[1];
        if (String(choice[0]) === String(value)) option.selected = true;
        input.appendChild(option);
      });
    } else {
      input.type = options.type || "text";
      if (options.min !== undefined) input.min = options.min;
      if (options.step !== undefined) input.step = options.step;
      input.value = value === undefined || value === null ? "" : String(value);
    }
    wrapper.appendChild(input);
    return { wrapper: wrapper, input: input };
  }

  function makeRemoveButton(callback) {
    var button = create("button", "mini danger ghost", "Удалить строку");
    button.type = "button";
    button.addEventListener("click", callback);
    return button;
  }

  function bindInputs(inputs, callback) {
    inputs.forEach(function (input) {
      input.addEventListener("input", callback);
      input.addEventListener("change", callback);
    });
  }

  function renderObjectSection(host, textarea, builder) {
    var value = safeParse(textarea, {});
    if (!value || typeof value !== "object" || Array.isArray(value)) value = {};
    builder(host, value, function (next) { writeJson(textarea, next); });
  }

  function renderFormula(host, textarea) {
    renderObjectSection(host, textarea, function (root, value, commit) {
      var grid = create("div", "guided-grid");
      var code = makeInput("Код формулы", value.code || "");
      var offset = makeInput(
        "Просрочка начинается через, дней",
        value.delay_start_offset_days === undefined ? "" : value.delay_start_offset_days,
        { type: "number", min: "0", step: "1" }
      );
      var divisor = makeInput("Делитель", value.divisor || "");
      var quant = makeInput("Шаг денежного округления", value.money_quant || "");
      var rounding = makeInput(
        "Режим округления",
        value.rounding_mode || "",
        {
          type: "select",
          choices: [
            ["", "Не выбрано"],
            ["ROUND_HALF_UP", "ROUND_HALF_UP"],
            ["ROUND_HALF_EVEN", "ROUND_HALF_EVEN"],
            ["ROUND_DOWN", "ROUND_DOWN"],
            ["ROUND_UP", "ROUND_UP"]
          ]
        }
      );
      var stage = makeInput(
        "Когда округлять",
        value.rounding_stage || "",
        {
          type: "select",
          choices: [["", "Не выбрано"], ["total", "Только итог"], ["segment", "Каждый сегмент"]]
        }
      );
      var refs = makeInput("Источники (ID через запятую)", refsText(value.source_refs));
      [code, offset, divisor, quant, rounding, stage, refs].forEach(function (field) {
        grid.appendChild(field.wrapper);
      });
      root.appendChild(grid);
      var inputs = [code.input, offset.input, divisor.input, quant.input, rounding.input, stage.input, refs.input];
      bindInputs(inputs, function () {
        commit({
          code: code.input.value.trim(),
          delay_start_offset_days: offset.input.value === "" ? null : Number(offset.input.value),
          divisor: divisor.input.value.trim(),
          money_quant: quant.input.value.trim(),
          rounding_mode: rounding.input.value,
          rounding_stage: stage.input.value,
          source_refs: splitRefs(refs.input.value)
        });
      });
    });
  }

  function renderRatePolicy(host, textarea) {
    renderObjectSection(host, textarea, function (root, value, commit) {
      var grid = create("div", "guided-grid");
      var mode = makeInput(
        "Принцип ставки",
        value.mode || "",
        { type: "select", choices: [["", "Не выбрано"], ["due_date", "Ставка на дату исполнения обязательства"]] }
      );
      var from = makeInput("Справочник подтверждён с", value.coverage_from || "", { type: "date" });
      var through = makeInput("Справочник подтверждён по", value.coverage_through || "", { type: "date" });
      var refs = makeInput("Источники (ID через запятую)", refsText(value.source_refs));
      [mode, from, through, refs].forEach(function (field) { grid.appendChild(field.wrapper); });
      root.appendChild(grid);
      bindInputs([mode.input, from.input, through.input, refs.input], function () {
        commit({
          mode: mode.input.value,
          coverage_from: from.input.value,
          coverage_through: through.input.value,
          source_refs: splitRefs(refs.input.value)
        });
      });
    });
  }

  function listDefaults(kind) {
    if (kind === "rate") return { code: "", start: "", end: "", rate: "", source_refs: [] };
    if (kind === "moratorium") return { code: "", start: "", end: "", client_types: "*", unique_object: null, source_refs: [] };
    if (kind === "cap") return { code: "", start: "", end: "", cap: "", client_types: "*", unique_object: null, source_refs: [] };
    if (kind === "stop") return { code: "", flag: "", message: "", source_refs: [] };
    return {};
  }

  function renderList(host, textarea, kind) {
    var list = safeParse(textarea, []);
    if (!Array.isArray(list)) list = [];

    function commit() {
      writeJson(textarea, list);
    }

    function draw() {
      host.replaceChildren();
      var listHost = create("div", "guided-list");
      host.appendChild(listHost);

      if (!list.length) {
        listHost.appendChild(create("div", "guided-empty", "Строк пока нет."));
      }

      list.forEach(function (item, index) {
        var row = create("div", "guided-row");
        var grid = create("div", "guided-grid");
        var fields = {};

        function add(name, label, value, options) {
          var field = makeInput(label, value, options);
          fields[name] = field.input;
          grid.appendChild(field.wrapper);
        }

        add("code", "Код правила", item.code || "");
        if (kind === "rate") {
          add("start", "Начало периода", item.start || "", { type: "date" });
          add("end", "Конец периода", item.end || "", { type: "date" });
          add("rate", "Ставка, доля (например 0.15)", item.rate || "");
          add("source_refs", "Источники (ID)", refsText(item.source_refs));
        } else if (kind === "moratorium" || kind === "cap") {
          add("start", "Начало периода", item.start || "", { type: "date" });
          add("end", "Конец периода", item.end || "", { type: "date" });
          if (kind === "cap") add("cap", "Предельная ставка, доля", item.cap || "");
          add("client_types", "Типы клиентов (* или consumer, other)", clientTypesText(item.client_types));
          add(
            "unique_object",
            "Уникальный объект",
            item.unique_object === true ? "true" : item.unique_object === false ? "false" : "",
            { type: "select", choices: [["", "Любой"], ["false", "Нет"], ["true", "Да"]] }
          );
          add("source_refs", "Источники (ID)", refsText(item.source_refs));
        } else if (kind === "stop") {
          add("flag", "Технический флаг", item.flag || "");
          add("message", "Причина ручной проверки", item.message || "");
          add("source_refs", "Источники (ID)", refsText(item.source_refs));
        }

        row.appendChild(grid);
        row.appendChild(makeRemoveButton(function () {
          list.splice(index, 1);
          commit();
          draw();
        }));

        bindInputs(Object.values(fields), function () {
          if (kind === "rate") {
            list[index] = {
              code: fields.code.value.trim(),
              start: fields.start.value,
              end: fields.end.value || null,
              rate: fields.rate.value.trim(),
              source_refs: splitRefs(fields.source_refs.value)
            };
          } else if (kind === "moratorium" || kind === "cap") {
            var next = {
              code: fields.code.value.trim(),
              start: fields.start.value,
              end: fields.end.value,
              client_types: parseClientTypes(fields.client_types.value),
              unique_object: boolOrNull(fields.unique_object.value),
              source_refs: splitRefs(fields.source_refs.value)
            };
            if (kind === "cap") next.cap = fields.cap.value.trim();
            list[index] = next;
          } else if (kind === "stop") {
            list[index] = {
              code: fields.code.value.trim(),
              flag: fields.flag.value.trim(),
              message: fields.message.value.trim(),
              source_refs: splitRefs(fields.source_refs.value)
            };
          }
          commit();
        });

        listHost.appendChild(row);
      });

      var addButton = create("button", "add-row", "+ Добавить строку");
      addButton.type = "button";
      addButton.addEventListener("click", function () {
        list.push(listDefaults(kind));
        commit();
        draw();
      });
      host.appendChild(addButton);
    }

    draw();
  }

  function renderClientTypes(host, textarea) {
    renderObjectSection(host, textarea, function (root, value, commit) {
      var grid = create("div", "guided-two");
      var groups = [
        ["consumer", "Гражданин для личных нужд"],
        ["other", "Иной участник"]
      ];
      var controls = {};
      groups.forEach(function (group) {
        var code = group[0];
        var card = create("div", "guided-row");
        card.appendChild(create("h4", "", group[1]));
        var inner = create("div", "guided-grid");
        var current = value[code] || {};
        var multiplier = makeInput("Коэффициент", current.multiplier || "");
        var refs = makeInput("Источники (ID)", refsText(current.source_refs));
        inner.appendChild(multiplier.wrapper);
        inner.appendChild(refs.wrapper);
        card.appendChild(inner);
        grid.appendChild(card);
        controls[code] = { multiplier: multiplier.input, refs: refs.input };
      });
      root.appendChild(grid);
      var inputs = [];
      Object.keys(controls).forEach(function (key) {
        inputs.push(controls[key].multiplier, controls[key].refs);
      });
      bindInputs(inputs, function () {
        commit({
          consumer: {
            multiplier: controls.consumer.multiplier.value.trim(),
            source_refs: splitRefs(controls.consumer.refs.value)
          },
          other: {
            multiplier: controls.other.multiplier.value.trim(),
            source_refs: splitRefs(controls.other.refs.value)
          }
        });
      });
    });
  }

  function renderUniqueObject(host, textarea) {
    renderObjectSection(host, textarea, function (root, value, commit) {
      var grid = create("div", "guided-grid");
      var enabled = makeInput(
        "Автоматическая ветка разрешена",
        value.enabled === true ? "true" : value.enabled === false ? "false" : "",
        { type: "select", choices: [["", "Не выбрано"], ["true", "Да"], ["false", "Нет — только юрист"]] }
      );
      var multiplier = makeInput("Коэффициент", value.multiplier || "");
      var cap = makeInput("Предельная сумма от цены ДДУ, доля", value.amount_cap_percent || "");
      var months = makeInput(
        "Ручная проверка после, месяцев",
        value.manual_review_after_months === undefined ? "" : value.manual_review_after_months,
        { type: "number", min: "1", step: "1" }
      );
      var refs = makeInput("Источники (ID)", refsText(value.source_refs));
      [enabled, multiplier, cap, months, refs].forEach(function (field) { grid.appendChild(field.wrapper); });
      root.appendChild(grid);
      bindInputs([enabled.input, multiplier.input, cap.input, months.input, refs.input], function () {
        commit({
          enabled: enabled.input.value === "true" ? true : enabled.input.value === "false" ? false : null,
          multiplier: multiplier.input.value.trim(),
          amount_cap_percent: cap.input.value.trim(),
          manual_review_after_months: months.input.value === "" ? null : Number(months.input.value),
          source_refs: splitRefs(refs.input.value)
        });
      });
    });
  }

  function renderSources(host, textarea) {
    var sourceObject = safeParse(textarea, {});
    if (!sourceObject || typeof sourceObject !== "object" || Array.isArray(sourceObject)) sourceObject = {};
    var list = Object.keys(sourceObject).map(function (code) {
      return Object.assign({ code: code }, sourceObject[code] || {});
    });

    function commit() {
      var next = {};
      list.forEach(function (item) {
        var code = String(item.code || "").trim();
        if (!code) return;
        next[code] = {
          title: String(item.title || "").trim(),
          locator: String(item.locator || "").trim(),
          url: String(item.url || "").trim(),
          checked_at: String(item.checked_at || "").trim()
        };
      });
      writeJson(textarea, next);
    }

    function draw() {
      host.replaceChildren();
      var listHost = create("div", "guided-list");
      host.appendChild(listHost);
      if (!list.length) listHost.appendChild(create("div", "guided-empty", "Источников пока нет."));

      list.forEach(function (item, index) {
        var row = create("div", "guided-row");
        var grid = create("div", "guided-grid");
        var code = makeInput("ID источника", item.code || "");
        var title = makeInput("Наименование", item.title || "");
        var locator = makeInput("Точное основание: статья / пункт / раздел / таблица", item.locator || "");
        locator.wrapper.classList.add("guided-wide");
        var url = makeInput("HTTPS-ссылка", item.url || "", { type: "url" });
        url.wrapper.classList.add("guided-wide");
        var checked = makeInput("Проверено", item.checked_at || "", { type: "date" });
        [code, title, locator, url, checked].forEach(function (field) { grid.appendChild(field.wrapper); });
        row.appendChild(grid);
        row.appendChild(makeRemoveButton(function () {
          list.splice(index, 1);
          commit();
          draw();
        }));
        bindInputs([code.input, title.input, locator.input, url.input, checked.input], function () {
          list[index] = {
            code: code.input.value.trim(),
            title: title.input.value.trim(),
            locator: locator.input.value.trim(),
            url: url.input.value.trim(),
            checked_at: checked.input.value
          };
          commit();
        });
        listHost.appendChild(row);
      });

      var addButton = create("button", "add-row", "+ Добавить источник");
      addButton.type = "button";
      addButton.addEventListener("click", function () {
        list.push({ code: "", title: "", locator: "", url: "", checked_at: "" });
        commit();
        draw();
      });
      host.appendChild(addButton);
    }

    draw();
  }

  function renderControlExamples(host, textarea) {
    var list = safeParse(textarea, []);
    if (!Array.isArray(list)) list = [];

    function commit() {
      writeJson(textarea, list);
    }

    function draw() {
      host.replaceChildren();
      var listHost = create("div", "guided-list");
      host.appendChild(listHost);
      if (!list.length) listHost.appendChild(create("div", "guided-empty", "Контрольных примеров пока нет."));

      list.forEach(function (item, index) {
        var input = item.input || {};
        var expected = item.expected || {};
        var row = create("div", "guided-row");
        var grid = create("div", "guided-grid");
        var fields = {};

        function add(name, label, value, options) {
          var field = makeInput(label, value, options);
          fields[name] = field.input;
          grid.appendChild(field.wrapper);
        }

        add("code", "Код примера", item.code || "");
        add("source_refs", "Источники (ID)", refsText(item.source_refs));
        add("contract_price", "Цена ДДУ", input.contract_price || "");
        add("planned_transfer_date", "Дата исполнения обязательства", input.planned_transfer_date || "", { type: "date" });
        add("calculation_date", "Дата расчёта", input.calculation_date || "", { type: "date" });
        add("object_transferred", "Объект передан", String(input.object_transferred === undefined ? true : input.object_transferred), { type: "select", choices: [["true", "Да"], ["false", "Нет"]] });
        add("actual_transfer_date", "Фактическая передача", input.actual_transfer_date || "", { type: "date" });
        add("client_type", "Тип участника", input.client_type || "consumer", { type: "select", choices: [["consumer", "Гражданин"], ["other", "Иной"]] });
        add("unique_object", "Уникальный объект", String(input.unique_object === true), { type: "select", choices: [["false", "Нет"], ["true", "Да"]] });
        add("penalty_amount", "Ожидаемая сумма", expected.penalty_amount || "");
        add("delay_days_total", "Всего дней", expected.delay_days_total === undefined ? "" : expected.delay_days_total, { type: "number" });
        add("delay_days_chargeable", "Начисляемых дней", expected.delay_days_chargeable === undefined ? "" : expected.delay_days_chargeable, { type: "number" });
        add("moratorium_days", "Исключённых дней", expected.moratorium_days === undefined ? "" : expected.moratorium_days, { type: "number" });
        add("base_rate", "Ожидаемая базовая ставка", expected.base_rate || "");
        add("amount_cap_applied", "Ожидается cap суммы", String(expected.amount_cap_applied === true), { type: "select", choices: [["false", "Нет"], ["true", "Да"]] });
        add("manual_review_required", "Ожидается ручная проверка", String(expected.manual_review_required === true), { type: "select", choices: [["false", "Нет"], ["true", "Да"]] });

        row.appendChild(grid);
        row.appendChild(makeRemoveButton(function () {
          list.splice(index, 1);
          commit();
          draw();
        }));

        bindInputs(Object.values(fields), function () {
          var expectedNext = {};
          if (fields.penalty_amount.value.trim()) expectedNext.penalty_amount = fields.penalty_amount.value.trim();
          ["delay_days_total", "delay_days_chargeable", "moratorium_days"].forEach(function (name) {
            if (fields[name].value !== "") expectedNext[name] = Number(fields[name].value);
          });
          if (fields.base_rate.value.trim()) expectedNext.base_rate = fields.base_rate.value.trim();
          expectedNext.amount_cap_applied = fields.amount_cap_applied.value === "true";
          if (fields.manual_review_required.value === "true") expectedNext.manual_review_required = true;

          list[index] = {
            code: fields.code.value.trim(),
            source_refs: splitRefs(fields.source_refs.value),
            input: {
              contract_price: fields.contract_price.value.trim(),
              planned_transfer_date: fields.planned_transfer_date.value,
              calculation_date: fields.calculation_date.value,
              object_transferred: fields.object_transferred.value === "true",
              actual_transfer_date: fields.actual_transfer_date.value || null,
              client_type: fields.client_type.value,
              unique_object: fields.unique_object.value === "true"
            },
            expected: expectedNext
          };
          commit();
        });

        listHost.appendChild(row);
      });

      var addButton = create("button", "add-row", "+ Добавить контрольный пример");
      addButton.type = "button";
      addButton.addEventListener("click", function () {
        list.push({
          code: "",
          source_refs: [],
          input: {
            contract_price: "",
            planned_transfer_date: "",
            calculation_date: "",
            object_transferred: true,
            actual_transfer_date: null,
            client_type: "consumer",
            unique_object: false
          },
          expected: { amount_cap_applied: false }
        });
        commit();
        draw();
      });
      host.appendChild(addButton);
    }

    draw();
  }

  function initSection(section) {
    var key = section.getAttribute("data-rule-section");
    var textarea = one(section, "textarea[data-rule-json]");
    var host = one(section, "[data-guided-editor]");
    if (!key || !textarea || !host) return;

    if (key === "formula") return renderFormula(host, textarea);
    if (key === "rate_policy") return renderRatePolicy(host, textarea);
    if (key === "rate_directory") return renderList(host, textarea, "rate");
    if (key === "moratoria") return renderList(host, textarea, "moratorium");
    if (key === "rate_caps") return renderList(host, textarea, "cap");
    if (key === "client_types") return renderClientTypes(host, textarea);
    if (key === "unique_object") return renderUniqueObject(host, textarea);
    if (key === "stop_factors") return renderList(host, textarea, "stop");
    if (key === "control_examples") return renderControlExamples(host, textarea);
    if (key === "sources") return renderSources(host, textarea);
  }

  all(document, "[data-rule-section]").forEach(initSection);
})();
</script>
"""


__all__ = ["CALCULATOR_RULE_EDITOR_SCRIPT"]
