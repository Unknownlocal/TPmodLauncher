/* SPDX-License-Identifier: MPL-2.0 */
/* Keep Frida's native handles here; the Python controller owns launch policy. */
#include "frida-core.h"
#include <signal.h>
#include <stdio.h>
#include <string.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

#define MAX_FRAME (8 * 1024 * 1024)
#define MAX_PENDING 16

typedef struct {
  guint id, pid;
  FridaSession *value;
  gboolean notified;
} Session;
typedef struct {
  guint id, session;
  FridaScript *value;
} Script;

static FridaDeviceManager *manager;
static FridaDevice *device;
static GMainLoop *loop;
static GHashTable *sessions, *scripts, *owned, *held;
static GAsyncQueue *requests;
static GMutex wire_lock, operation_lock;
static GCond operation_changed;
static GCancellable *active;
static gint64 active_id, deadline;
static gint input_closed, pending;
static gboolean busy, finished, watchdog_stopped;
static guint next_handle = 1;
static int exit_code;

/* Wrap an object in a JSON node, transferring ownership to the node. */
static JsonNode *object_node(JsonObject *object) {
  JsonNode *node = json_node_new(JSON_NODE_OBJECT);
  json_node_take_object(node, object);
  return node;
}

/* Only this function writes stdout: each line is a complete private RPC frame. */
static void write_frame(JsonObject *object) {
  JsonNode *node = object_node(object);
  gchar *text = json_to_string(node, FALSE);
  g_mutex_lock(&wire_lock);
  if (strlen(text) > MAX_FRAME || fputs(text, stdout) == EOF || fputc('\n', stdout) == EOF ||
      fflush(stdout) == EOF)
    g_atomic_int_set(&input_closed, 1);
  g_mutex_unlock(&wire_lock);
  g_free(text);
  json_node_unref(node);
}

/* Events carry stable handle IDs so a reused process ID cannot name an old session. */
static void event(const gchar *method, JsonObject *params) {
  JsonObject *frame = json_object_new();
  json_object_set_string_member(frame, "jsonrpc", "2.0");
  json_object_set_string_member(frame, "method", method);
  json_object_set_object_member(frame, "params", params);
  write_frame(frame);
}

/* Return a value or an error for the request with this ID. */
static void reply(gint64 id, JsonNode *result, GError *error) {
  JsonObject *frame = json_object_new();
  json_object_set_string_member(frame, "jsonrpc", "2.0");
  json_object_set_int_member(frame, "id", id);
  if (error != NULL) {
    JsonObject *detail = json_object_new();
    json_object_set_int_member(detail, "code", -32000);
    json_object_set_string_member(detail, "message", error->message);
    json_object_set_object_member(frame, "error", detail);
    g_clear_error(&error);
    if (result != NULL)
      json_node_unref(result);
  } else {
    if (result == NULL)
      result = json_node_new(JSON_NODE_NULL);
    json_object_set_member(frame, "result", result);
  }
  write_frame(frame);
}

/* Bad input is rejected before any corresponding Frida operation runs. */
static void invalid(GError **error, const gchar *message) {
  g_set_error_literal(error, G_IO_ERROR, G_IO_ERROR_INVALID_ARGUMENT, message);
}

/* Require the stated JSON type rather than relying on JSON-GLib's coercions. */
static JsonNode *member(JsonObject *object, const gchar *key, GType type, GError **error) {
  JsonNode *node = json_object_get_member(object, key);
  if (node == NULL || !JSON_NODE_HOLDS_VALUE(node) || json_node_get_value_type(node) != type) {
    invalid(error, "Missing or incorrectly typed parameter");
    return NULL;
  }
  return node;
}

/* Convert argv or envp to the string array expected by Core. */
static gchar **strings(JsonObject *params, const gchar *key, gint *length, GError **error) {
  JsonNode *node = json_object_get_member(params, key);
  if (node == NULL || !JSON_NODE_HOLDS_ARRAY(node)) {
    invalid(error, "Expected a string array");
    return NULL;
  }
  JsonArray *array = json_node_get_array(node);
  *length = (gint)json_array_get_length(array);
  gchar **values = g_new0(gchar *, *length + 1);
  for (gint i = 0; i < *length; i++) {
    JsonNode *item = json_array_get_element(array, i);
    if (!JSON_NODE_HOLDS_VALUE(item) || json_node_get_value_type(item) != G_TYPE_STRING) {
      invalid(error, "Array contains a non-string value");
      g_strfreev(values);
      return NULL;
    }
    values[i] = json_node_dup_string(item);
  }
  return values;
}

/* A separate watchdog can cancel a synchronous Core call even if its event loop is busy. */
static gpointer watchdog(gpointer unused) {
  (void)unused;
  g_mutex_lock(&operation_lock);
  while (!watchdog_stopped) {
    if (active == NULL)
      g_cond_wait(&operation_changed, &operation_lock);
    else if (!g_cond_wait_until(&operation_changed, &operation_lock, deadline)) {
      g_cancellable_cancel(active);
      g_cond_wait(&operation_changed, &operation_lock);
    }
  }
  g_mutex_unlock(&operation_lock);
  return NULL;
}

/* Arm cancellation for one request; timeout is in milliseconds, bounded by the reader. */
static GCancellable *begin_operation(gint64 id, gint64 timeout) {
  g_mutex_lock(&operation_lock);
  active = g_cancellable_new();
  active_id = id;
  deadline = g_get_monotonic_time() + timeout * 1000;
  if (id > 0 && g_atomic_int_get(&input_closed))
    g_cancellable_cancel(active);
  g_cond_signal(&operation_changed);
  g_mutex_unlock(&operation_lock);
  return active;
}

/* Remove the watchdog's reference only after the Core call has returned. */
static void end_operation(void) {
  g_mutex_lock(&operation_lock);
  g_clear_object(&active);
  g_cond_signal(&operation_changed);
  g_mutex_unlock(&operation_lock);
}

/* Mark an owned child before publishing it, so it can be attached or released immediately. */
static void child_added(FridaDevice *source, FridaChild *child, gpointer unused) {
  (void)source;
  (void)unused;
  guint pid = frida_child_get_pid(child), parent = frida_child_get_parent_pid(child);
  if (!g_hash_table_contains(owned, GUINT_TO_POINTER(parent)))
    return;
  g_hash_table_add(owned, GUINT_TO_POINTER(pid));
  g_hash_table_add(held, GUINT_TO_POINTER(pid));
  JsonObject *params = json_object_new();
  json_object_set_int_member(params, "pid", pid);
  json_object_set_int_member(params, "parent_pid", parent);
  GEnumClass *klass = g_type_class_ref(FRIDA_TYPE_CHILD_ORIGIN);
  GEnumValue *origin = g_enum_get_value(klass, frida_child_get_origin(child));
  json_object_set_string_member(params, "origin", origin ? origin->value_nick : "unknown");
  g_type_class_unref(klass);
  event("child-added", params);
}

/* Core retains this record until worker shutdown; callbacks never reference freed handles. */
static void detached(FridaSession *source, FridaSessionDetachReason reason, FridaCrash *crash,
                     Session *session) {
  (void)source;
  (void)crash;
  session->notified = TRUE;
  JsonObject *params = json_object_new();
  json_object_set_int_member(params, "session", session->id);
  GEnumClass *klass = g_type_class_ref(FRIDA_TYPE_SESSION_DETACH_REASON);
  GEnumValue *value = g_enum_get_value(klass, reason);
  json_object_set_string_member(params, "reason", value ? value->value_nick : "unknown");
  g_type_class_unref(klass);
  event("detached", params);
}

/* Forward script messages as data; this worker does not interpret Python or mod requests. */
static void message(FridaScript *source, const gchar *text, GBytes *data, Script *script) {
  (void)source;
  GError *error = NULL;
  JsonNode *node = strlen(text) <= MAX_FRAME / 2 ? json_from_string(text, &error) : NULL;
  if (node == NULL || data != NULL) {
    g_clear_error(&error);
    if (node != NULL)
      json_node_unref(node);
    node = object_node(json_object_new());
    json_object_set_string_member(json_node_get_object(node), "type", "error");
    json_object_set_string_member(json_node_get_object(node), "description",
                                  "Unsupported binary or oversized script message");
  }
  JsonObject *params = json_object_new();
  json_object_set_int_member(params, "script", script->id);
  json_object_set_member(params, "message", node);
  event("message", params);
}

/* Never allow game stdout to corrupt the worker's RPC stdout. */
static void output(FridaDevice *source, guint pid, gint fd, GBytes *data, gpointer unused) {
  (void)source;
  (void)unused;
  if (!g_hash_table_contains(owned, GUINT_TO_POINTER(pid)))
    return;
  gsize size;
  const guchar *bytes = g_bytes_get_data(data, &size);
  for (gsize offset = 0; offset < size; offset += 65536) {
    gchar *encoded = g_base64_encode(bytes + offset, MIN(size - offset, 65536));
    JsonObject *params = json_object_new();
    json_object_set_int_member(params, "pid", pid);
    json_object_set_int_member(params, "fd", fd);
    json_object_set_string_member(params, "data", encoded);
    event("output", params);
    g_free(encoded);
  }
}

/* Device loss must end controller tracking, not leave it waiting for nonexistent events. */
static void lost(FridaDevice *source, gpointer unused) {
  (void)source;
  (void)unused;
  event("lost", json_object_new());
}

/* Find only handles created by this worker; numeric PIDs are not session handles. */
static gpointer handle(GHashTable *table, JsonObject *params, const gchar *key, GError **error) {
  JsonNode *node = member(params, key, G_TYPE_INT64, error);
  if (node == NULL)
    return NULL;
  gint64 id = json_node_get_int(node);
  gpointer value =
      id > 0 && id <= G_MAXUINT ? g_hash_table_lookup(table, GUINT_TO_POINTER((guint)id)) : NULL;
  if (value == NULL)
    invalid(error, "Unknown native handle");
  return value;
}

/* List process paths for the existing Python controller's identity checks. */
static JsonNode *processes(JsonObject *params, GCancellable *cancel, GError **error) {
  FridaProcessQueryOptions *options = frida_process_query_options_new();
  frida_process_query_options_set_scope(options, FRIDA_SCOPE_METADATA);
  JsonNode *selected = json_object_get_member(params, "pids");
  if (selected != NULL) {
    if (!JSON_NODE_HOLDS_ARRAY(selected)) {
      invalid(error, "pids must be an array");
      g_object_unref(options);
      return NULL;
    }
    JsonArray *pids = json_node_get_array(selected);
    for (guint i = 0; i < json_array_get_length(pids); i++) {
      JsonNode *pid = json_array_get_element(pids, i);
      if (!JSON_NODE_HOLDS_VALUE(pid) || json_node_get_value_type(pid) != G_TYPE_INT64 ||
          json_node_get_int(pid) <= 0 || json_node_get_int(pid) > G_MAXUINT) {
        invalid(error, "Invalid process ID");
        g_object_unref(options);
        return NULL;
      }
      frida_process_query_options_select_pid(options, (guint)json_node_get_int(pid));
    }
  }
  FridaProcessList *list = frida_device_enumerate_processes_sync(device, options, cancel, error);
  g_object_unref(options);
  if (list == NULL)
    return NULL;
  JsonArray *result = json_array_new();
  for (gint i = 0; i < frida_process_list_size(list); i++) {
    FridaProcess *process = frida_process_list_get(list, i);
    JsonObject *item = json_object_new(), *metadata = json_object_new();
    json_object_set_int_member(item, "pid", frida_process_get_pid(process));
    GVariant *path = g_hash_table_lookup(frida_process_get_parameters(process), "path");
    if (path != NULL && g_variant_is_of_type(path, G_VARIANT_TYPE_STRING))
      json_object_set_string_member(metadata, "path", g_variant_get_string(path, NULL));
    json_object_set_object_member(item, "parameters", metadata);
    json_array_add_object_element(result, item);
    g_object_unref(process);
  }
  g_object_unref(list);
  JsonNode *node = json_node_new(JSON_NODE_ARRAY);
  json_node_take_array(node, result);
  return node;
}

/* Execute the small trusted transport API; no shell, arbitrary DLL or global attach command. */
static JsonNode *execute(const gchar *method, JsonObject *params, GCancellable *cancel,
                         GError **error) {
  if (strcmp(method, "hello") == 0) {
    JsonObject *result = json_object_new();
    json_object_set_int_member(result, "protocol", 1);
    json_object_set_string_member(result, "frida", frida_version_string());
    return object_node(result);
  }
  if (strcmp(method, "processes") == 0)
    return processes(params, cancel, error);
  if (strcmp(method, "spawn") == 0) {
    if (g_hash_table_size(owned) != 0) {
      invalid(error, "This worker already spawned a root process");
      return NULL;
    }
    gint argc = 0, envc = 0;
    gchar **argv = strings(params, "argv", &argc, error);
    if (argv == NULL)
      return NULL;
    gchar **envp = strings(params, "envp", &envc, error);
    JsonNode *cwd = *error == NULL ? member(params, "cwd", G_TYPE_STRING, error) : NULL;
    if (envp == NULL || cwd == NULL || argc == 0 || !g_path_is_absolute(argv[0])) {
      if (*error == NULL)
        invalid(error, "Expected an absolute executable and nonempty argv");
      g_strfreev(argv);
      g_strfreev(envp);
      return NULL;
    }
    FridaSpawnOptions *options = frida_spawn_options_new();
    frida_spawn_options_set_argv(options, argv, argc);
    frida_spawn_options_set_envp(options, envp, envc);
    frida_spawn_options_set_cwd(options, json_node_get_string(cwd));
    frida_spawn_options_set_stdio(options, FRIDA_STDIO_PIPE);
    guint pid = frida_device_spawn_sync(device, argv[0], options, cancel, error);
    g_object_unref(options);
    g_strfreev(argv);
    g_strfreev(envp);
    if (*error != NULL)
      return NULL;
    g_hash_table_add(owned, GUINT_TO_POINTER(pid));
    g_hash_table_add(held, GUINT_TO_POINTER(pid));
    JsonNode *node = json_node_new(JSON_NODE_VALUE);
    json_node_set_int(node, pid);
    return node;
  }
  if (strcmp(method, "pending") == 0) {
    FridaChildList *list = frida_device_enumerate_pending_children_sync(device, cancel, error);
    if (list == NULL)
      return NULL;
    JsonArray *array = json_array_new();
    for (gint i = 0; i < frida_child_list_size(list); i++) {
      FridaChild *child = frida_child_list_get(list, i);
      guint pid = frida_child_get_pid(child), parent = frida_child_get_parent_pid(child);
      if (g_hash_table_contains(owned, GUINT_TO_POINTER(parent))) {
        g_hash_table_add(owned, GUINT_TO_POINTER(pid));
        g_hash_table_add(held, GUINT_TO_POINTER(pid));
        JsonObject *item = json_object_new();
        json_object_set_int_member(item, "pid", pid);
        json_object_set_int_member(item, "parent_pid", parent);
        json_array_add_object_element(array, item);
      }
      g_object_unref(child);
    }
    g_object_unref(list);
    JsonNode *node = json_node_new(JSON_NODE_ARRAY);
    json_node_take_array(node, array);
    return node;
  }
  if (strcmp(method, "attach") == 0 || strcmp(method, "resume") == 0) {
    JsonNode *node = member(params, "pid", G_TYPE_INT64, error);
    if (node == NULL)
      return NULL;
    gint64 number = json_node_get_int(node);
    if (number <= 0 || number > G_MAXUINT ||
        !g_hash_table_contains(owned, GUINT_TO_POINTER((guint)number))) {
      invalid(error, "Process is outside this worker's spawned chain");
      return NULL;
    }
    guint pid = (guint)number;
    if (strcmp(method, "resume") == 0) {
      frida_device_resume_sync(device, pid, cancel, error);
      if (*error == NULL || g_error_matches(*error, FRIDA_ERROR, FRIDA_ERROR_PROCESS_NOT_FOUND))
        g_hash_table_remove(held, GUINT_TO_POINTER(pid));
      return NULL;
    }
    FridaSession *value = frida_device_attach_sync(device, pid, NULL, cancel, error);
    if (value == NULL)
      return NULL;
    Session *session = g_new0(Session, 1);
    session->id = next_handle++;
    session->pid = pid;
    session->value = value;
    g_hash_table_insert(sessions, GUINT_TO_POINTER(session->id), session);
    g_signal_connect(value, "detached", G_CALLBACK(detached), session);
    JsonObject *result = json_object_new();
    json_object_set_int_member(result, "handle", session->id);
    json_object_set_boolean_member(result, "detached", frida_session_is_detached(value));
    return object_node(result);
  }
  if (g_str_has_prefix(method, "session.")) {
    Session *session = handle(sessions, params, "session", error);
    if (session == NULL)
      return NULL;
    if (strcmp(method, "session.state") == 0) {
      JsonNode *node = json_node_new(JSON_NODE_VALUE);
      json_node_set_boolean(node, frida_session_is_detached(session->value));
      return node;
    }
    if (strcmp(method, "session.enable") == 0)
      frida_session_enable_child_gating_sync(session->value, cancel, error);
    else if (strcmp(method, "session.disable") == 0)
      frida_session_disable_child_gating_sync(session->value, cancel, error);
    else if (strcmp(method, "session.detach") == 0)
      frida_session_detach_sync(session->value, cancel, error);
    else if (strcmp(method, "session.create") == 0) {
      JsonNode *source = member(params, "source", G_TYPE_STRING, error);
      if (source == NULL)
        return NULL;
      FridaScriptOptions *options = frida_script_options_new();
      frida_script_options_set_name(options, "modLauncher-hook-bridge");
      frida_script_options_set_runtime(options, FRIDA_SCRIPT_RUNTIME_QJS);
      FridaScript *value = frida_session_create_script_sync(
          session->value, json_node_get_string(source), options, cancel, error);
      g_object_unref(options);
      if (value == NULL)
        return NULL;
      Script *script = g_new0(Script, 1);
      script->id = next_handle++;
      script->session = session->id;
      script->value = value;
      g_hash_table_insert(scripts, GUINT_TO_POINTER(script->id), script);
      g_signal_connect(value, "message", G_CALLBACK(message), script);
      JsonNode *node = json_node_new(JSON_NODE_VALUE);
      json_node_set_int(node, script->id);
      return node;
    } else
      invalid(error, "Unknown session operation");
    return NULL;
  }
  if (g_str_has_prefix(method, "script.")) {
    Script *script = handle(scripts, params, "script", error);
    if (script == NULL)
      return NULL;
    if (strcmp(method, "script.state") == 0) {
      JsonNode *node = json_node_new(JSON_NODE_VALUE);
      json_node_set_boolean(node, frida_script_is_destroyed(script->value));
      return node;
    }
    if (strcmp(method, "script.load") == 0)
      frida_script_load_sync(script->value, cancel, error);
    else if (strcmp(method, "script.unload") == 0)
      frida_script_unload_sync(script->value, cancel, error);
    else if (strcmp(method, "script.post") == 0) {
      JsonNode *node = json_object_get_member(params, "message");
      if (node == NULL || !JSON_NODE_HOLDS_OBJECT(node)) {
        invalid(error, "Script post requires an object");
        return NULL;
      }
      gchar *text = json_to_string(node, FALSE);
      frida_script_post(script->value, text, NULL);
      g_free(text);
    } else
      invalid(error, "Unknown script operation");
    return NULL;
  }
  invalid(error, "Unknown worker operation");
  return NULL;
}

/* EOF or close releases pending children and native sessions; it never kills game processes. */
static void cleanup(void) {
  GError *error = NULL;
  /* One deadline bounds the whole cleanup, regardless of the number of sessions. */
  GCancellable *cancel = begin_operation(-1, 8000);
  FridaChildList *children = frida_device_enumerate_pending_children_sync(device, cancel, &error);
  if (children != NULL) {
    for (gint i = 0; i < frida_child_list_size(children); i++) {
      FridaChild *child = frida_child_list_get(children, i);
      if (g_hash_table_contains(owned, GUINT_TO_POINTER(frida_child_get_parent_pid(child))))
        g_hash_table_add(held, GUINT_TO_POINTER(frida_child_get_pid(child)));
      g_object_unref(child);
    }
    g_object_unref(children);
  }
  g_clear_error(&error);
  /* Core calls may dispatch child callbacks; iterate a snapshot, not the changing table. */
  GList *pids = g_hash_table_get_keys(held);
  for (GList *item = pids; item != NULL; item = item->next) {
    frida_device_resume_sync(device, GPOINTER_TO_UINT(item->data), cancel, &error);
    /* A child may have already exited; session cleanup still runs. */
    g_clear_error(&error);
  }
  g_list_free(pids);
  GHashTableIter iter;
  gpointer value;
  g_hash_table_iter_init(&iter, sessions);
  while (g_hash_table_iter_next(&iter, NULL, &value)) {
    Session *session = value;
    if (!frida_session_is_detached(session->value)) {
      frida_session_disable_child_gating_sync(session->value, cancel, &error);
      g_clear_error(&error);
      frida_session_detach_sync(session->value, cancel, &error);
      if (error != NULL && !frida_session_is_detached(session->value)) {
        fprintf(stderr, "Native session cleanup failed: %s\n", error->message);
        exit_code = 1;
      }
      g_clear_error(&error);
    }
  }
  if (g_cancellable_is_cancelled(cancel)) {
    fprintf(stderr, "Native cleanup exceeded its deadline\n");
    exit_code = 1;
  }
  end_operation();
}

/* Drop finished native handles between operations, keeping replacement tracking bounded. */
static void prune_handles(void) {
  GHashTableIter iter;
  gpointer value;
  g_hash_table_iter_init(&iter, scripts);
  while (g_hash_table_iter_next(&iter, NULL, &value)) {
    Script *script = value;
    Session *session = g_hash_table_lookup(sessions, GUINT_TO_POINTER(script->session));
    if (frida_script_is_destroyed(script->value) || (session != NULL && session->notified)) {
      g_signal_handlers_disconnect_by_data(script->value, script);
      g_object_unref(script->value);
      g_free(script);
      g_hash_table_iter_remove(&iter);
    }
  }
  g_hash_table_iter_init(&iter, sessions);
  while (g_hash_table_iter_next(&iter, NULL, &value)) {
    Session *session = value;
    if (session->notified) {
      g_signal_handlers_disconnect_by_data(session->value, session);
      g_object_unref(session->value);
      g_free(session);
      g_hash_table_iter_remove(&iter);
    }
  }
}

/* Process one queued request at a time, while allowing Core's event callbacks to run. */
static gboolean service(gpointer unused) {
  (void)unused;
  if (busy || finished)
    return G_SOURCE_CONTINUE;
  prune_handles();
  if (g_atomic_int_get(&input_closed)) {
    busy = TRUE;
    cleanup();
    finished = TRUE;
    g_main_loop_quit(loop);
    return G_SOURCE_REMOVE;
  }
  JsonNode *node = g_async_queue_try_pop(requests);
  if (node == NULL)
    return G_SOURCE_CONTINUE;
  g_atomic_int_add(&pending, -1);
  busy = TRUE;
  JsonObject *request = json_node_get_object(node);
  gint64 id = json_object_get_int_member(request, "id");
  const gchar *method = json_object_get_string_member(request, "method");
  JsonObject *params = json_object_get_object_member(request, "params");
  if (strcmp(method, "close") == 0) {
    cleanup();
    reply(id, NULL, NULL);
    finished = TRUE;
    g_main_loop_quit(loop);
  } else {
    GCancellable *cancel = begin_operation(id, json_object_get_int_member(request, "timeout_ms"));
    GError *error = NULL;
    JsonNode *result = execute(method, params, cancel, &error);
    end_operation();
    reply(id, result, error);
  }
  json_node_unref(node);
  busy = FALSE;
  return finished ? G_SOURCE_REMOVE : G_SOURCE_CONTINUE;
}

/* Read bounded frames on a separate thread; cancel can interrupt an active synchronous call. */
static gpointer read_requests(gpointer unused) {
  (void)unused;
  gchar *line = g_malloc(MAX_FRAME + 2);
  while (fgets(line, MAX_FRAME + 2, stdin) != NULL) {
    gsize size = strlen(line);
    if (size == 0 || line[size - 1] != '\n')
      break;
    GError *error = NULL;
    JsonNode *node = json_from_string(line, &error);
    if (node == NULL || !JSON_NODE_HOLDS_OBJECT(node)) {
      g_clear_error(&error);
      if (node != NULL)
        json_node_unref(node);
      break;
    }
    JsonObject *request = json_node_get_object(node);
    JsonNode *method = member(request, "method", G_TYPE_STRING, &error);
    JsonNode *version = error == NULL ? member(request, "jsonrpc", G_TYPE_STRING, &error) : NULL;
    if (error != NULL || strcmp(json_node_get_string(version), "2.0") != 0) {
      g_clear_error(&error);
      json_node_unref(node);
      break;
    }
    if (strcmp(json_node_get_string(method), "$/cancelRequest") == 0) {
      JsonNode *params = json_object_get_member(request, "params");
      if (params == NULL || !JSON_NODE_HOLDS_OBJECT(params)) {
        json_node_unref(node);
        break;
      }
      JsonNode *id = member(json_node_get_object(params), "id", G_TYPE_INT64, &error);
      if (id != NULL) {
        g_mutex_lock(&operation_lock);
        if (active != NULL && active_id == json_node_get_int(id))
          g_cancellable_cancel(active);
        g_mutex_unlock(&operation_lock);
      }
      g_clear_error(&error);
      json_node_unref(node);
      continue;
    }
    JsonNode *id = member(request, "id", G_TYPE_INT64, &error);
    JsonNode *timeout = error == NULL ? member(request, "timeout_ms", G_TYPE_INT64, &error) : NULL;
    JsonNode *params = json_object_get_member(request, "params");
    if (error != NULL || json_node_get_int(id) <= 0 || json_node_get_int(timeout) < 1 ||
        json_node_get_int(timeout) > 10000 || params == NULL || !JSON_NODE_HOLDS_OBJECT(params) ||
        g_atomic_int_get(&pending) >= MAX_PENDING) {
      g_clear_error(&error);
      json_node_unref(node);
      break;
    }
    g_atomic_int_inc(&pending);
    g_async_queue_push(requests, node);
  }
  g_free(line);
  g_atomic_int_set(&input_closed, 1);
  g_mutex_lock(&operation_lock);
  if (active != NULL)
    g_cancellable_cancel(active);
  g_mutex_unlock(&operation_lock);
  return NULL;
}

/* The worker has no command-line attach mode: only its owning launcher's pipes control it. */
int main(int argc, char **argv) {
  (void)argv;
  if (argc != 1) {
    fprintf(stderr, "This worker is controlled through private launcher pipes.\n");
    return 2;
  }
#ifdef _WIN32
  _setmode(_fileno(stdin), _O_BINARY);
  _setmode(_fileno(stdout), _O_BINARY);
#else
  /* Parent pipe loss must reach cleanup instead of terminating us with SIGPIPE. */
  signal(SIGPIPE, SIG_IGN);
#endif
  frida_init();
  manager = frida_device_manager_new();
  GError *error = NULL;
  device = frida_device_manager_get_device_by_type_sync(manager, FRIDA_DEVICE_TYPE_LOCAL, 5000,
                                                        NULL, &error);
  if (device == NULL) {
    fprintf(stderr, "Local Frida device unavailable: %s\n", error->message);
    g_error_free(error);
    return 1;
  }
  sessions = g_hash_table_new(g_direct_hash, g_direct_equal);
  scripts = g_hash_table_new(g_direct_hash, g_direct_equal);
  owned = g_hash_table_new(g_direct_hash, g_direct_equal);
  held = g_hash_table_new(g_direct_hash, g_direct_equal);
  requests = g_async_queue_new_full((GDestroyNotify)json_node_unref);
  g_signal_connect(device, "child-added", G_CALLBACK(child_added), NULL);
  g_signal_connect(device, "output", G_CALLBACK(output), NULL);
  g_signal_connect(device, "lost", G_CALLBACK(lost), NULL);
  loop = g_main_loop_new(NULL, FALSE);
  GThread *timer = g_thread_new("rpc-watchdog", watchdog, NULL);
  GThread *reader = g_thread_new("rpc-input", read_requests, NULL);
  g_timeout_add(5, service, NULL);
  g_main_loop_run(loop);
  /* close may leave the input thread blocked until the parent closes its pipe. */
  g_thread_unref(reader);
  g_mutex_lock(&operation_lock);
  watchdog_stopped = TRUE;
  g_cond_signal(&operation_changed);
  g_mutex_unlock(&operation_lock);
  g_thread_join(timer);
  /* Let process exit release SDK allocations; no unbounded deinit wait after shutdown. */
  return exit_code;
}
