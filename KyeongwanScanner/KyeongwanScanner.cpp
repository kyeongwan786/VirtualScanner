#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <objidl.h>
#include <gdiplus.h>
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <algorithm>
#include <cwctype>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <mutex>
#include <string>
#include <vector>

#pragma comment(lib, "Gdiplus.lib")

using namespace Gdiplus;
namespace fs = std::filesystem;

// TWAIN 1.x ABI structures use two-byte packing and fixed-width strings.
#pragma pack(push, 2)
struct TW_VERSION_KW {
    uint16_t MajorNum;
    uint16_t MinorNum;
    uint16_t Language;
    uint16_t Country;
    char Info[34];
};

struct TW_IDENTITY_KW {
    uint32_t Id;
    TW_VERSION_KW Version;
    uint16_t ProtocolMajor;
    uint16_t ProtocolMinor;
    uint32_t SupportedGroups;
    char Manufacturer[34];
    char ProductFamily[34];
    char ProductName[34];
};

struct TW_STATUS_KW { uint16_t ConditionCode; uint16_t Reserved; };
struct TW_USERINTERFACE_KW { uint16_t ShowUI; uint16_t ModalUI; HANDLE hParent; };
struct TW_EVENT_KW { void* pEvent; uint16_t TWMessage; };
struct TW_PENDINGXFERS_KW { uint16_t Count; uint32_t Reserved; };
struct TW_CAPABILITY_KW { uint16_t Cap; uint16_t ConType; HGLOBAL hContainer; };
struct TW_ONEVALUE_KW { uint16_t ItemType; uint32_t Item; };
struct TW_ENUMERATION_KW {
    uint16_t ItemType;
    uint32_t NumItems;
    uint32_t CurrentIndex;
    uint32_t DefaultIndex;
    uint8_t ItemList[1];
};
struct TW_FIX32_KW { int16_t Whole; uint16_t Frac; };
struct TW_IMAGEINFO_KW {
    TW_FIX32_KW XResolution;
    TW_FIX32_KW YResolution;
    int32_t ImageWidth;
    int32_t ImageLength;
    int16_t SamplesPerPixel;
    int16_t BitsPerSample[8];
    int16_t BitsPerPixel;
    uint16_t Planar;
    int16_t PixelType;
    uint16_t Compression;
};
#pragma pack(pop)

// This project is intentionally Win32. These assertions guard the TWAIN 1.x
// ABI against accidental packing or field-size changes.
static_assert(sizeof(void*) == 4, "Build KyeongwanScanner for Win32 only.");
static_assert(sizeof(TW_VERSION_KW) == 42, "TW_VERSION ABI mismatch.");
static_assert(sizeof(TW_IDENTITY_KW) == 156, "TW_IDENTITY ABI mismatch.");
static_assert(sizeof(TW_STATUS_KW) == 4, "TW_STATUS ABI mismatch.");
static_assert(sizeof(TW_USERINTERFACE_KW) == 8, "TW_USERINTERFACE ABI mismatch.");
static_assert(sizeof(TW_EVENT_KW) == 6, "TW_EVENT ABI mismatch.");
static_assert(sizeof(TW_PENDINGXFERS_KW) == 6, "TW_PENDINGXFERS ABI mismatch.");
static_assert(sizeof(TW_CAPABILITY_KW) == 8, "TW_CAPABILITY ABI mismatch.");
static_assert(sizeof(TW_ONEVALUE_KW) == 6, "TW_ONEVALUE ABI mismatch.");
static_assert(offsetof(TW_ENUMERATION_KW, ItemList) == 14, "TW_ENUMERATION ABI mismatch.");
static_assert(sizeof(TW_IMAGEINFO_KW) == 42, "TW_IMAGEINFO ABI mismatch.");

namespace {
constexpr uint32_t DG_CONTROL = 0x0001;
constexpr uint32_t DG_IMAGE = 0x0002;
constexpr uint16_t DAT_EVENT = 0x0001;
constexpr uint16_t DAT_CAPABILITY = 0x0001;
constexpr uint16_t DAT_IDENTITY = 0x0003;
constexpr uint16_t DAT_PENDINGXFERS = 0x0005;
constexpr uint16_t DAT_STATUS = 0x0008;
constexpr uint16_t DAT_USERINTERFACE = 0x0009;
constexpr uint16_t DAT_IMAGEINFO = 0x0101;
constexpr uint16_t DAT_IMAGENATIVEXFER = 0x0104;

constexpr uint16_t MSG_GET = 0x0001;
constexpr uint16_t MSG_GETCURRENT = 0x0002;
constexpr uint16_t MSG_GETDEFAULT = 0x0003;
constexpr uint16_t MSG_SET = 0x0006;
constexpr uint16_t MSG_PROCESSEVENT = 0x0001;
constexpr uint16_t MSG_OPENDS = 0x0401;
constexpr uint16_t MSG_CLOSEDS = 0x0402;
constexpr uint16_t MSG_ENABLEDS = 0x0502;
constexpr uint16_t MSG_DISABLEDS = 0x0501;
constexpr uint16_t MSG_ENDXFER = 0x0701;
constexpr uint16_t MSG_RESET = 0x0702;
constexpr uint16_t MSG_XFERREADY = 0x0101;

constexpr uint16_t TWRC_SUCCESS = 0;
constexpr uint16_t TWRC_FAILURE = 1;
constexpr uint16_t TWRC_DSEVENT = 4;
constexpr uint16_t TWRC_NOTDSEVENT = 5;
constexpr uint16_t TWRC_XFERDONE = 6;
constexpr uint16_t TWCC_SUCCESS = 0;
constexpr uint16_t TWCC_LOWMEMORY = 2;
constexpr uint16_t TWCC_NODS = 3;
constexpr uint16_t TWCC_OPERATIONERROR = 5;
constexpr uint16_t TWCC_BADVALUE = 10;
constexpr uint16_t TWCC_SEQERROR = 11;
constexpr uint16_t TWCC_CAPUNSUPPORTED = 13;
constexpr uint16_t TWCC_CAPBADOPERATION = 14;

constexpr uint16_t TWON_ENUMERATION = 4;
constexpr uint16_t TWON_ONEVALUE = 5;
constexpr uint16_t TWTY_INT16 = 1;
constexpr uint16_t TWTY_UINT16 = 4;
constexpr uint16_t TWTY_FIX32 = 7;
constexpr uint16_t TWSX_NATIVE = 0;
constexpr uint16_t TWPT_RGB = 2;
constexpr uint16_t TWCP_NONE = 0;
constexpr uint16_t TWUN_INCHES = 0;
constexpr uint16_t CAP_XFERCOUNT = 0x0001;
constexpr uint16_t CAP_SUPPORTEDCAPS = 0x1005;
constexpr uint16_t ICAP_COMPRESSION = 0x0100;
constexpr uint16_t ICAP_PIXELTYPE = 0x0101;
constexpr uint16_t ICAP_BITDEPTH = 0x0102;
constexpr uint16_t ICAP_XFERMECH = 0x0103;
constexpr uint16_t ICAP_UNITS = 0x0104;
constexpr uint16_t ICAP_XRESOLUTION = 0x1118;
constexpr uint16_t ICAP_YRESOLUTION = 0x1119;
constexpr uint16_t TWON_LANGUAGE_USA = 13;
constexpr uint16_t TWCY_USA = 1;
constexpr uint16_t TWAIN_STATE_DS_OPEN = 4;
constexpr uint16_t TWAIN_STATE_DS_ENABLED = 5;
constexpr uint16_t TWAIN_STATE_TRANSFER_READY = 6;
constexpr uint16_t TWAIN_STATE_TRANSFERRING = 7;
constexpr DWORD kTraceRotateBytes = 1024 * 1024;

constexpr uint16_t kSupportedCaps[] = {
    CAP_SUPPORTEDCAPS, CAP_XFERCOUNT, ICAP_COMPRESSION, ICAP_PIXELTYPE,
    ICAP_BITDEPTH, ICAP_XFERMECH, ICAP_UNITS, ICAP_XRESOLUTION, ICAP_YRESOLUTION
};

LONG g_state = 3;
uint16_t g_lastCondition = TWCC_SUCCESS;
HWND g_parentWindow = nullptr;
bool g_eventPending = false;
std::vector<fs::path> g_pages;
fs::path g_jobDirectory;
size_t g_nextPage = 0;
uint16_t g_resolutionDpi = 175;
uint16_t g_transferCount = 0xffff; // -1: return all pages in the ready job.
std::once_flag g_gdiplusOnce;
ULONG_PTR g_gdiplusToken = 0;
std::mutex g_traceMutex;

bool IsCurrentPageAvailable();

bool ShouldTraceTwainCall(uint32_t dataGroup, uint16_t dataArgType, uint16_t message,
                          uint16_t result) {
    if (result == TWRC_FAILURE) return true;
    if (dataGroup == DG_CONTROL && dataArgType == DAT_IDENTITY)
        return message == MSG_OPENDS || message == MSG_CLOSEDS;
    if (dataGroup == DG_CONTROL && dataArgType == DAT_USERINTERFACE)
        return message == MSG_ENABLEDS || message == MSG_DISABLEDS;
    if (dataGroup == DG_CONTROL && dataArgType == DAT_EVENT)
        return result == TWRC_DSEVENT;
    if (dataGroup == DG_CONTROL && dataArgType == DAT_PENDINGXFERS)
        return message == MSG_ENDXFER || message == MSG_RESET;
    return dataGroup == DG_IMAGE &&
        (dataArgType == DAT_IMAGEINFO || dataArgType == DAT_IMAGENATIVEXFER);
}

void TraceTwainCall(uint32_t dataGroup, uint16_t dataArgType, uint16_t message,
                    uint16_t result, void* data) noexcept {
    if (!ShouldTraceTwainCall(dataGroup, dataArgType, message, result)) return;
    try {
        std::lock_guard<std::mutex> guard(g_traceMutex);
        wchar_t programData[MAX_PATH]{};
        const DWORD length = GetEnvironmentVariableW(L"ProgramData", programData, MAX_PATH);
        if (length == 0 || length >= MAX_PATH) return;

        wchar_t directory[MAX_PATH]{};
        if (swprintf_s(directory, L"%s\\KyeongwanScanner\\VirtualSource", programData) < 0) return;
        CreateDirectoryW((std::wstring(programData) + L"\\KyeongwanScanner").c_str(), nullptr);
        CreateDirectoryW(directory, nullptr);
        const std::wstring logPath = std::wstring(directory) + L"\\twain-diagnostics.log";

        WIN32_FILE_ATTRIBUTE_DATA attributes{};
        if (GetFileAttributesExW(logPath.c_str(), GetFileExInfoStandard, &attributes)) {
            const ULONGLONG size = (static_cast<ULONGLONG>(attributes.nFileSizeHigh) << 32) |
                                  attributes.nFileSizeLow;
            if (size >= kTraceRotateBytes) {
                const std::wstring previousPath = logPath + L".1";
                DeleteFileW(previousPath.c_str());
                MoveFileW(logPath.c_str(), previousPath.c_str());
            }
        }

        HANDLE file = CreateFileW(logPath.c_str(), FILE_APPEND_DATA,
                                  FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                  nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (file == INVALID_HANDLE_VALUE) return;

        SYSTEMTIME now{};
        GetLocalTime(&now);
        char detail[80]{};
        if (dataGroup == DG_CONTROL && dataArgType == DAT_CAPABILITY && data) {
            const auto* capability = static_cast<const TW_CAPABILITY_KW*>(data);
            sprintf_s(detail, " cap=0x%04X", capability->Cap);
        } else if (dataGroup == DG_CONTROL && dataArgType == DAT_PENDINGXFERS && data &&
                   result != TWRC_FAILURE) {
            const auto* pending = static_cast<const TW_PENDINGXFERS_KW*>(data);
            sprintf_s(detail, " pending=%u", pending->Count);
        }

        char line[320]{};
        const int lineLength = sprintf_s(
            line, "%04u-%02u-%02u %02u:%02u:%02u pid=%lu tid=%lu dg=0x%08lX dat=0x%04X msg=0x%04X rc=%u cc=%u state=%ld page=%zu/%zu%s\r\n",
            now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond,
            GetCurrentProcessId(), GetCurrentThreadId(), static_cast<unsigned long>(dataGroup),
            dataArgType, message, result, g_lastCondition, g_state,
            IsCurrentPageAvailable() ? g_nextPage + 1 : 0, g_pages.size(), detail);
        if (lineLength > 0) {
            DWORD written = 0;
            WriteFile(file, line, static_cast<DWORD>(lineLength), &written, nullptr);
            FlushFileBuffers(file);
        }
        CloseHandle(file);
    } catch (...) {
        // Diagnostics are best-effort and must never affect scanner operation.
    }
}

TW_IDENTITY_KW MakeIdentity() {
    TW_IDENTITY_KW identity{};
    identity.Id = 1;
    identity.Version.MajorNum = 1;
    identity.Version.MinorNum = 9;
    identity.Version.Language = TWON_LANGUAGE_USA;
    identity.Version.Country = TWCY_USA;
    strcpy_s(identity.Version.Info, "Kyeongwan Scanner 0.2");
    identity.ProtocolMajor = 1;
    identity.ProtocolMinor = 9;
    identity.SupportedGroups = DG_CONTROL | DG_IMAGE;
    strcpy_s(identity.Manufacturer, "Kyeongwan");
    strcpy_s(identity.ProductFamily, "Virtual Scanner");
    strcpy_s(identity.ProductName, "kyeongwan-scanner");
    return identity;
}

void SetCondition(uint16_t condition) { g_lastCondition = condition; }

fs::path GetQueueRoot() {
    wchar_t programData[MAX_PATH]{};
    DWORD length = GetEnvironmentVariableW(L"ProgramData", programData, MAX_PATH);
    if (length == 0 || length >= MAX_PATH) return {};
    return fs::path(programData) / L"KyeongwanScanner" / L"VirtualSource" / L"ready";
}

bool ReadNumberField(const std::string& json, const char* key, uint32_t& result) {
    const std::string name = std::string("\"") + key + "\"";
    size_t pos = json.find(name);
    if (pos == std::string::npos) return false;
    pos = json.find(':', pos + name.size());
    if (pos == std::string::npos) return false;
    ++pos;
    while (pos < json.size() && (json[pos] == ' ' || json[pos] == '\t' || json[pos] == '\r' || json[pos] == '\n')) ++pos;
    if (pos == json.size() || json[pos] < '0' || json[pos] > '9') return false;
    uint64_t value = 0;
    while (pos < json.size() && json[pos] >= '0' && json[pos] <= '9') {
        value = value * 10 + static_cast<unsigned>(json[pos++] - '0');
        if (value > UINT32_MAX) return false;
    }
    result = static_cast<uint32_t>(value);
    return true;
}

bool ReadJob(const fs::path& directory, std::vector<fs::path>& pages, uint16_t& dpi) {
    std::ifstream manifest(directory / L"manifest.json", std::ios::binary);
    if (!manifest) return false;
    std::string json((std::istreambuf_iterator<char>(manifest)), std::istreambuf_iterator<char>());
    uint32_t expectedPages = 0;
    uint32_t imageDpi = 175;
    if (!ReadNumberField(json, "page_count", expectedPages) || expectedPages == 0 || expectedPages > 100) return false;
    ReadNumberField(json, "dpi", imageDpi);
    if (imageDpi < 72 || imageDpi > 600) imageDpi = 175;

    std::vector<fs::path> found;
    for (const auto& entry : fs::directory_iterator(directory)) {
        if (!entry.is_regular_file()) continue;
        std::wstring extension = entry.path().extension().wstring();
        for (auto& ch : extension) ch = static_cast<wchar_t>(towlower(ch));
        if (extension == L".jpg" || extension == L".jpeg") found.push_back(entry.path());
    }
    std::sort(found.begin(), found.end());
    if (found.size() != expectedPages) return false;
    pages = std::move(found);
    dpi = static_cast<uint16_t>(imageDpi);
    return true;
}

bool FindNextJob() {
    const fs::path root = GetQueueRoot();
    if (root.empty() || !fs::is_directory(root)) return false;
    std::vector<std::pair<fs::file_time_type, fs::path>> candidates;
    for (const auto& entry : fs::directory_iterator(root)) {
        if (!entry.is_directory()) continue;
        std::error_code ec;
        const auto modified = fs::last_write_time(entry.path(), ec);
        if (!ec) candidates.emplace_back(modified, entry.path());
    }
    std::sort(candidates.begin(), candidates.end(), [](const auto& left, const auto& right) {
        return left.first < right.first;
    });
    for (const auto& candidate : candidates) {
        std::vector<fs::path> pages;
        uint16_t dpi = 175;
        if (ReadJob(candidate.second, pages, dpi)) {
            g_jobDirectory = candidate.second;
            g_pages = std::move(pages);
            g_nextPage = 0;
            g_resolutionDpi = dpi;
            return true;
        }
    }
    return false;
}

bool RemoveCurrentJob() {
    if (g_jobDirectory.empty()) return false;
    try {
        std::error_code error;
        const uintmax_t removed = fs::remove_all(g_jobDirectory, error);
        return !error && removed > 0;
    } catch (...) {
        return false;
    }
}

void ClearCurrentJob() {
    g_pages.clear();
    g_jobDirectory.clear();
    g_nextPage = 0;
}

TW_FIX32_KW MakeFix32(uint16_t whole) {
    return TW_FIX32_KW{static_cast<int16_t>(whole), 0};
}

bool DecodeFix32(uint32_t raw, uint16_t& whole) {
    TW_FIX32_KW value{};
    std::memcpy(&value, &raw, sizeof(value));
    const double number = static_cast<double>(value.Whole) + static_cast<double>(value.Frac) / 65536.0;
    if (number < 72.0 || number > 600.0) return false;
    whole = static_cast<uint16_t>(number + 0.5);
    return true;
}

uint32_t EncodeFix32(uint16_t whole) {
    const TW_FIX32_KW value = MakeFix32(whole);
    uint32_t raw = 0;
    std::memcpy(&raw, &value, sizeof(raw));
    return raw;
}

HGLOBAL MakeOneValue(uint16_t itemType, uint32_t item) {
    HGLOBAL handle = GlobalAlloc(GHND, sizeof(TW_ONEVALUE_KW));
    if (!handle) return nullptr;
    auto* value = static_cast<TW_ONEVALUE_KW*>(GlobalLock(handle));
    if (!value) { GlobalFree(handle); return nullptr; }
    value->ItemType = itemType;
    value->Item = item;
    GlobalUnlock(handle);
    return handle;
}

HGLOBAL MakeSupportedCaps() {
    const size_t itemOffset = offsetof(TW_ENUMERATION_KW, ItemList);
    const size_t bytes = itemOffset + sizeof(kSupportedCaps);
    HGLOBAL handle = GlobalAlloc(GHND, bytes);
    if (!handle) return nullptr;
    auto* enumeration = static_cast<TW_ENUMERATION_KW*>(GlobalLock(handle));
    if (!enumeration) { GlobalFree(handle); return nullptr; }
    enumeration->ItemType = TWTY_UINT16;
    enumeration->NumItems = static_cast<uint32_t>(sizeof(kSupportedCaps) / sizeof(kSupportedCaps[0]));
    enumeration->CurrentIndex = 0;
    enumeration->DefaultIndex = 0;
    std::memcpy(enumeration->ItemList, kSupportedCaps, sizeof(kSupportedCaps));
    GlobalUnlock(handle);
    return handle;
}

bool GetCapabilityValue(uint16_t cap, uint16_t& itemType, uint32_t& item) {
    switch (cap) {
    case CAP_XFERCOUNT: itemType = TWTY_INT16; item = 0xffffffffu; return true;
    case ICAP_COMPRESSION: itemType = TWTY_UINT16; item = TWCP_NONE; return true;
    case ICAP_PIXELTYPE: itemType = TWTY_UINT16; item = TWPT_RGB; return true;
    case ICAP_BITDEPTH: itemType = TWTY_UINT16; item = 8; return true;
    case ICAP_XFERMECH: itemType = TWTY_UINT16; item = TWSX_NATIVE; return true;
    case ICAP_UNITS: itemType = TWTY_UINT16; item = TWUN_INCHES; return true;
    case ICAP_XRESOLUTION:
    case ICAP_YRESOLUTION: itemType = TWTY_FIX32; item = EncodeFix32(g_resolutionDpi); return true;
    default: return false;
    }
}

uint16_t GetCapability(TW_CAPABILITY_KW* capability, uint16_t message) {
    if (!capability) { SetCondition(TWCC_BADVALUE); return TWRC_FAILURE; }
    if (message == MSG_SET) {
        if (g_state != TWAIN_STATE_DS_OPEN || !capability->hContainer) {
            SetCondition(TWCC_SEQERROR); return TWRC_FAILURE;
        }
        auto* value = static_cast<TW_ONEVALUE_KW*>(GlobalLock(capability->hContainer));
        if (!value) { SetCondition(TWCC_BADVALUE); return TWRC_FAILURE; }
        bool accepted = false;
        if (capability->Cap == ICAP_XFERMECH && value->ItemType == TWTY_UINT16 && value->Item == TWSX_NATIVE) {
            accepted = true;
        } else if (capability->Cap == CAP_XFERCOUNT && value->ItemType == TWTY_INT16 && value->Item == 0xffffffffu) {
            accepted = true;
        } else if ((capability->Cap == ICAP_XRESOLUTION || capability->Cap == ICAP_YRESOLUTION) &&
                   value->ItemType == TWTY_FIX32) {
            uint16_t dpi = 0;
            if (DecodeFix32(value->Item, dpi)) { g_resolutionDpi = dpi; accepted = true; }
        } else {
            uint16_t type = 0; uint32_t current = 0;
            accepted = GetCapabilityValue(capability->Cap, type, current) &&
                value->ItemType == type && value->Item == current;
        }
        GlobalUnlock(capability->hContainer);
        SetCondition(accepted ? TWCC_SUCCESS : TWCC_BADVALUE);
        return accepted ? TWRC_SUCCESS : TWRC_FAILURE;
    }

    if (message != MSG_GET && message != MSG_GETCURRENT && message != MSG_GETDEFAULT) {
        SetCondition(TWCC_CAPBADOPERATION); return TWRC_FAILURE;
    }
    if (capability->Cap == CAP_SUPPORTEDCAPS) {
        capability->ConType = TWON_ENUMERATION;
        capability->hContainer = MakeSupportedCaps();
    } else {
        uint16_t itemType = 0; uint32_t item = 0;
        if (!GetCapabilityValue(capability->Cap, itemType, item)) {
            SetCondition(TWCC_CAPUNSUPPORTED); return TWRC_FAILURE;
        }
        capability->ConType = TWON_ONEVALUE;
        capability->hContainer = MakeOneValue(itemType, item);
    }
    if (!capability->hContainer) { SetCondition(TWCC_LOWMEMORY); return TWRC_FAILURE; }
    SetCondition(TWCC_SUCCESS);
    return TWRC_SUCCESS;
}

HGLOBAL LoadJpegAsDib(const fs::path& path, uint32_t& width, uint32_t& height) {
    std::call_once(g_gdiplusOnce, [] {
        GdiplusStartupInput input;
        GdiplusStartup(&g_gdiplusToken, &input, nullptr);
    });
    Bitmap bitmap(path.c_str(), FALSE);
    if (bitmap.GetLastStatus() != Ok) return nullptr;
    width = bitmap.GetWidth();
    height = bitmap.GetHeight();
    if (width == 0 || height == 0 || width > 30000 || height > 30000) return nullptr;

    const uint64_t rowBytes = (static_cast<uint64_t>(width) * 3 + 3) & ~uint64_t(3);
    const uint64_t imageBytes = rowBytes * height;
    const uint64_t totalBytes = sizeof(BITMAPINFOHEADER) + imageBytes;
    if (totalBytes > SIZE_MAX || imageBytes > UINT32_MAX) return nullptr;

    Rect rectangle(0, 0, static_cast<INT>(width), static_cast<INT>(height));
    BitmapData bitmapData{};
    if (bitmap.LockBits(&rectangle, ImageLockModeRead, PixelFormat24bppRGB, &bitmapData) != Ok) return nullptr;

    HGLOBAL dib = GlobalAlloc(GHND, static_cast<SIZE_T>(totalBytes));
    if (!dib) { bitmap.UnlockBits(&bitmapData); return nullptr; }
    auto* bytes = static_cast<uint8_t*>(GlobalLock(dib));
    if (!bytes) {
        GlobalFree(dib);
        bitmap.UnlockBits(&bitmapData);
        return nullptr;
    }

    BITMAPINFOHEADER header{};
    header.biSize = sizeof(BITMAPINFOHEADER);
    header.biWidth = static_cast<LONG>(width);
    header.biHeight = static_cast<LONG>(height); // positive means bottom-up DIB rows.
    header.biPlanes = 1;
    header.biBitCount = 24;
    header.biCompression = BI_RGB;
    header.biSizeImage = static_cast<DWORD>(imageBytes);
    std::memcpy(bytes, &header, sizeof(header));
    uint8_t* pixels = bytes + sizeof(header);
    const uint64_t sourceRowBytes = static_cast<uint64_t>(width) * 3;
    for (uint32_t y = 0; y < height; ++y) {
        const auto* sourceRow = static_cast<const uint8_t*>(bitmapData.Scan0) +
            static_cast<ptrdiff_t>(y) * bitmapData.Stride;
        uint8_t* destinationRow = pixels + static_cast<uint64_t>(height - 1 - y) * rowBytes;
        std::memcpy(destinationRow, sourceRow, static_cast<size_t>(sourceRowBytes));
    }
    GlobalUnlock(dib);
    bitmap.UnlockBits(&bitmapData);
    return dib;
}

bool IsCurrentPageAvailable() {
    return !g_jobDirectory.empty() && g_nextPage < g_pages.size();
}

uint16_t ReturnFailure(uint16_t condition) {
    SetCondition(condition);
    return TWRC_FAILURE;
}
}

// TWAIN DSM resolves this export by name; ordinal 1 supports older DSM builds.
static uint16_t WINAPI DispatchDS_Entry(
    TW_IDENTITY_KW* /*origin*/, uint32_t dataGroup, uint16_t dataArgType,
    uint16_t message, void* data) {
    try {
    if (dataGroup == DG_CONTROL && dataArgType == DAT_STATUS && message == MSG_GET && data) {
        auto* status = static_cast<TW_STATUS_KW*>(data);
        status->ConditionCode = g_lastCondition;
        status->Reserved = 0;
        g_lastCondition = TWCC_SUCCESS;
        return TWRC_SUCCESS;
    }

    if (dataGroup == DG_CONTROL && dataArgType == DAT_IDENTITY && data) {
        if (message == MSG_GET) {
            *static_cast<TW_IDENTITY_KW*>(data) = MakeIdentity();
            SetCondition(TWCC_SUCCESS);
            return TWRC_SUCCESS;
        }
        if (message == MSG_OPENDS) {
            if (g_state != 3) return ReturnFailure(TWCC_SEQERROR);
            g_state = TWAIN_STATE_DS_OPEN;
            *static_cast<TW_IDENTITY_KW*>(data) = MakeIdentity();
            ClearCurrentJob();
            g_eventPending = false;
            g_parentWindow = nullptr;
            SetCondition(TWCC_SUCCESS);
            return TWRC_SUCCESS;
        }
        if (message == MSG_CLOSEDS) {
            if (g_state != TWAIN_STATE_DS_OPEN) return ReturnFailure(TWCC_SEQERROR);
            g_state = 3;
            ClearCurrentJob();
            g_eventPending = false;
            g_parentWindow = nullptr;
            SetCondition(TWCC_SUCCESS);
            return TWRC_SUCCESS;
        }
    }

    if (dataGroup == DG_CONTROL && dataArgType == DAT_CAPABILITY) {
        if (g_state < TWAIN_STATE_DS_OPEN || g_state > TWAIN_STATE_TRANSFER_READY) return ReturnFailure(TWCC_SEQERROR);
        return GetCapability(static_cast<TW_CAPABILITY_KW*>(data), message);
    }

    if (dataGroup == DG_CONTROL && dataArgType == DAT_USERINTERFACE && data) {
        auto* ui = static_cast<TW_USERINTERFACE_KW*>(data);
        if (message == MSG_ENABLEDS) {
            if (g_state != TWAIN_STATE_DS_OPEN) return ReturnFailure(TWCC_SEQERROR);
            g_parentWindow = static_cast<HWND>(ui->hParent);
            if (!FindNextJob()) return ReturnFailure(TWCC_NODS);
            g_state = TWAIN_STATE_DS_ENABLED;
            g_eventPending = true;
            if (!g_parentWindow || !PostMessageW(g_parentWindow, WM_NULL, 0, 0)) {
                g_state = TWAIN_STATE_DS_OPEN;
                g_eventPending = false;
                ClearCurrentJob();
                return ReturnFailure(TWCC_OPERATIONERROR);
            }
            SetCondition(TWCC_SUCCESS);
            return TWRC_SUCCESS;
        }
        if (message == MSG_DISABLEDS) {
            if (g_state != TWAIN_STATE_DS_ENABLED) return ReturnFailure(TWCC_SEQERROR);
            g_state = TWAIN_STATE_DS_OPEN;
            g_eventPending = false;
            ClearCurrentJob(); // Keep an incomplete job in ready so the application can retry it.
            SetCondition(TWCC_SUCCESS);
            return TWRC_SUCCESS;
        }
    }

    if (dataGroup == DG_CONTROL && dataArgType == DAT_EVENT && message == MSG_PROCESSEVENT && data) {
        if (g_state != TWAIN_STATE_DS_ENABLED) return ReturnFailure(TWCC_SEQERROR);
        auto* event = static_cast<TW_EVENT_KW*>(data);
        auto* windowsMessage = static_cast<MSG*>(event->pEvent);
        if (g_eventPending && windowsMessage && windowsMessage->message == WM_NULL) {
            event->TWMessage = MSG_XFERREADY;
            g_eventPending = false;
            g_state = TWAIN_STATE_TRANSFER_READY;
            SetCondition(TWCC_SUCCESS);
            return TWRC_DSEVENT;
        }
        return TWRC_NOTDSEVENT;
    }

    if (dataGroup == DG_CONTROL && dataArgType == DAT_PENDINGXFERS && data) {
        auto* pending = static_cast<TW_PENDINGXFERS_KW*>(data);
        if (message == MSG_GET && (g_state == TWAIN_STATE_TRANSFER_READY || g_state == TWAIN_STATE_TRANSFERRING)) {
            const size_t remaining = IsCurrentPageAvailable() ? g_pages.size() - g_nextPage : 0;
            pending->Count = static_cast<uint16_t>(remaining > 0xffff ? 0xffff : remaining);
            pending->Reserved = 0;
            SetCondition(TWCC_SUCCESS);
            return TWRC_SUCCESS;
        }
        if (message == MSG_ENDXFER && g_state == TWAIN_STATE_TRANSFERRING) {
            if (g_nextPage < g_pages.size()) ++g_nextPage;
            const size_t remaining = g_pages.size() - g_nextPage;
            pending->Count = static_cast<uint16_t>(remaining > 0xffff ? 0xffff : remaining);
            pending->Reserved = 0;
            if (remaining == 0) {
                if (!RemoveCurrentJob()) return ReturnFailure(TWCC_OPERATIONERROR);
                ClearCurrentJob();
                g_state = TWAIN_STATE_DS_ENABLED;
            } else {
                g_state = TWAIN_STATE_TRANSFER_READY;
            }
            SetCondition(TWCC_SUCCESS);
            return TWRC_SUCCESS;
        }
        if (message == MSG_RESET && g_state == TWAIN_STATE_TRANSFER_READY) {
            pending->Count = 0;
            pending->Reserved = 0;
            ClearCurrentJob(); // Preserve the job so a failed scan can be retried.
            g_state = TWAIN_STATE_DS_ENABLED;
            SetCondition(TWCC_SUCCESS);
            return TWRC_SUCCESS;
        }
    }

    if (dataGroup == DG_IMAGE && dataArgType == DAT_IMAGEINFO && message == MSG_GET && data) {
        if (g_state != TWAIN_STATE_TRANSFER_READY || !IsCurrentPageAvailable()) return ReturnFailure(TWCC_SEQERROR);
        std::call_once(g_gdiplusOnce, [] { GdiplusStartupInput input; GdiplusStartup(&g_gdiplusToken, &input, nullptr); });
        Bitmap bitmap(g_pages[g_nextPage].c_str(), FALSE);
        if (bitmap.GetLastStatus() != Ok) return ReturnFailure(TWCC_OPERATIONERROR);
        auto* info = static_cast<TW_IMAGEINFO_KW*>(data);
        *info = {};
        info->XResolution = MakeFix32(g_resolutionDpi);
        info->YResolution = MakeFix32(g_resolutionDpi);
        info->ImageWidth = static_cast<int32_t>(bitmap.GetWidth());
        info->ImageLength = static_cast<int32_t>(bitmap.GetHeight());
        info->SamplesPerPixel = 3;
        info->BitsPerSample[0] = info->BitsPerSample[1] = info->BitsPerSample[2] = 8;
        info->BitsPerPixel = 24;
        info->Planar = 0;
        info->PixelType = TWPT_RGB;
        info->Compression = TWCP_NONE;
        SetCondition(TWCC_SUCCESS);
        return TWRC_SUCCESS;
    }

    if (dataGroup == DG_IMAGE && dataArgType == DAT_IMAGENATIVEXFER && message == MSG_GET && data) {
        if (g_state != TWAIN_STATE_TRANSFER_READY || !IsCurrentPageAvailable()) return ReturnFailure(TWCC_SEQERROR);
        uint32_t width = 0, height = 0;
        HGLOBAL dib = LoadJpegAsDib(g_pages[g_nextPage], width, height);
        if (!dib) return ReturnFailure(TWCC_OPERATIONERROR);
        *static_cast<HGLOBAL*>(data) = dib;
        g_state = TWAIN_STATE_TRANSFERRING;
        SetCondition(TWCC_SUCCESS);
        return TWRC_XFERDONE;
    }

    return ReturnFailure(TWCC_CAPUNSUPPORTED);
    } catch (...) {
        // Never allow filesystem, allocation, or image decoder exceptions to
        // escape through the TWAIN C ABI into the business application.
        return ReturnFailure(TWCC_OPERATIONERROR);
    }
}

// TWAIN DSM resolves this export by name; ordinal 1 supports older DSM builds.
extern "C" __declspec(dllexport) uint16_t WINAPI DS_Entry(
    TW_IDENTITY_KW* origin, uint32_t dataGroup, uint16_t dataArgType,
    uint16_t message, void* data) {
    const uint16_t result = DispatchDS_Entry(origin, dataGroup, dataArgType, message, data);
    TraceTwainCall(dataGroup, dataArgType, message, result, data);
    return result;
}

BOOL WINAPI DllMain(HINSTANCE, DWORD, LPVOID) { return TRUE; }
