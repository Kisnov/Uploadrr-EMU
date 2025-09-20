from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from src.utils.console import log


class ContactSheetGenerator:
    def __init__(self, config, base_dir, media_info_handler):
        self.config = config
        self.base_dir = Path(base_dir)
        self.mihandle = media_info_handler

    async def generate_contact_sheet(self, screenshots, meta, output_path, rows, columns):
        """Generate contact sheet using cached MediaInfo metadata and screenshots."""
        if not screenshots:
            log.warning("No screenshots provided for contact sheet generation")
            return

        media_info = self.mihandle._cache.get(meta["uuid"], {})
        general_track = None
        video_track = None

        for track in media_info.get("media", {}).get("track", []):
            if track.get("@type") == "General":
                general_track = track
            elif track.get("@type") == "Video":
                video_track = track

        filename = general_track.get("FileName", "Unknown File") if general_track else "Unknown File"
        duration_str = general_track.get("Duration_String4", "00:00:00") if general_track else "00:00:00"
        file_size_str = general_track.get("FileSize_String3", "0 B") if general_track else "0 B"

        width = video_track.get("Width", "") if video_track else ""
        height = video_track.get("Height", "") if video_track else ""
        resolution_str = f"{width} x {height}" if width and height else "Unknown"
        bitrate = video_track.get("BitRate_String", "") if video_track else ""
        fps = video_track.get("FrameRate_String", "") if video_track else ""
        bitrate_str = bitrate if fps == "" else f"{bitrate} @ {fps}"

        if not filename or filename == "Unknown File":
            filename = Path(meta["uuid"]).stem

        cached_screenshots = meta.get("cached_screenshots", [])
        if not cached_screenshots:
            log.warning(f"No cached screenshots found for UUID {meta['uuid']}, falling back to input list")
            full_screenshot_list = screenshots
        else:
            full_screenshot_list = [
                ss
                for ss in cached_screenshots
                if isinstance(ss, dict)
                and ss.get("local_file")
                and Path(self.base_dir / "tmp" / meta["uuid"] / ss["local_file"]).exists()
            ]
            full_screenshot_list.sort(key=lambda x: x.get("ss_time", 0))

        if not full_screenshot_list:
            full_screenshot_list = screenshots

        total_needed = rows * columns
        if len(full_screenshot_list) < total_needed:
            log.warning(
                f"Only {len(full_screenshot_list)} screenshots available, but contact sheet requires {total_needed}. Using all."
            )
            total_needed = len(full_screenshot_list)

        screenshots_for_sheet = full_screenshot_list[:total_needed]

        first_img_path = screenshots_for_sheet[0]["local_file"]
        full_first_img_path = self.base_dir / "tmp" / meta["uuid"] / first_img_path

        try:
            with Image.open(full_first_img_path) as img:
                original_width, original_height = img.size
        except Exception as e:
            log.error(f"Failed to open first screenshot {full_first_img_path}: {e}")
            original_width, original_height = 1920, 1080

        thumbnail_width = 400
        thumbnail_height = int(thumbnail_width * (original_height / original_width))
        margin = 3
        header_height = 100
        footer_height = 10

        total_width = 2 * margin + columns * thumbnail_width + (columns - 1) * margin
        total_height = header_height + margin + rows * thumbnail_height + (rows - 1) * margin + footer_height

        sheet = Image.new("RGB", (total_width, total_height), (0, 0, 0))
        draw = ImageDraw.Draw(sheet)

        # --- HEADER BG --- #
        header_bg_color = (20, 20, 20)
        draw.rectangle([(0, 0), (total_width, header_height)], fill=header_bg_color)

        # --- FONTS --- #
        font_size_header = 22
        font_size_meta = 18
        font_size_logo_text = 32
        myfont = self.config["SCREENS"].get("font")
        font_path = myfont if myfont else str(self.base_dir / "src" / "screenshots" / "fonts" / "dosis.ttf")
        logo_font_path = str(self.base_dir / "src" / "screenshots" / "fonts" / "sexandbreakfast.ttf")
        try:
            font_header = ImageFont.truetype(font_path, font_size_header)
            font_meta = ImageFont.truetype(font_path, font_size_meta)
            logo_font = ImageFont.truetype(logo_font_path, font_size_logo_text)
        except Exception as e:
            log.warning(f"Failed to load fonts: {e}, using default")
            font_header = ImageFont.load_default()
            font_meta = ImageFont.load_default()
            logo_font = ImageFont.load_default()

        # --- HEADER TXT --- #
        title_text = f"   {filename}"
        title_bbox = draw.textbbox((0, 0), title_text, font=font_header)
        title_x = margin
        title_y = (header_height - title_bbox[3]) // 2 - 10
        draw.text((title_x, title_y), title_text, fill=(255, 255, 255), font=font_header)

        fileinfo = (
            f"   Resolution: {resolution_str}  |  Duration: {duration_str}  |  File Size: {file_size_str}  |  Bitrate: {bitrate_str}"
        )
        meta1_x = margin
        meta1_y = title_y + title_bbox[3] + 5
        draw.text((meta1_x, meta1_y), fileinfo, fill=(200, 200, 200), font=font_meta)

        # --- LOGO (Image or Text) --- #
        logo_path = self.config["SCREENS"]["contact_sheet"].get("logo")
        logo = None
        logo_x = 0
        logo_y = 0

        if logo_path and Path(logo_path).exists():
            try:
                logo = Image.open(logo_path).convert("RGBA")
                max_logo_height = header_height * 0.7
                logo_ratio = logo.width / logo.height
                new_logo_height = min(max_logo_height, logo.height)
                new_logo_width = int(new_logo_height * logo_ratio)
                logo = logo.resize((new_logo_width, int(new_logo_height)), Image.Resampling.LANCZOS)
            except Exception as e:
                log.warning(f"Could not load logo {logo_path}: {e}")
                logo = None

        # If no valid image logo, use text-based logo
        if not logo:
            logo_text = self.config["SCREENS"]["contact_sheet"].get("logo_text", "uploadrr")
            logo_text_color = self.config["SCREENS"]["contact_sheet"].get("logo_text", {}).get("color", (255, 255, 255))
            if isinstance(logo_text_color, str):
                try:
                    logo_text_color = tuple(int(logo_text_color.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
                except ValueError:
                    log.warning(f"Invalid logo text color {logo_text_color}, defaulting to white")
                    logo_text_color = (255, 255, 255)
            elif not isinstance(logo_text_color, tuple) or len(logo_text_color) != 3:
                log.warning(f"Invalid logo text color format {logo_text_color}, defaulting to white")
                logo_text_color = (255, 255, 255)

            logo_bbox = draw.textbbox((0, 0), logo_text, font=logo_font)
            logo_width = logo_bbox[2] - logo_bbox[0]
            logo_height = logo_bbox[3] - logo_bbox[1]
        else:
            logo_width = logo.width
            logo_height = logo.height

        # Center logo over the last column
        last_column_index = columns - 1
        logo_position = int(margin + last_column_index * (thumbnail_width + margin) + (thumbnail_width * 2 / 3))
        logo_x = logo_position - logo_width // 2
        logo_y = (header_height - logo_height) // 2

        # Ensure logo doesn't go out of bounds
        logo_x = max(margin, min(logo_x, total_width - logo_width - margin))

        if logo:
            sheet.paste(logo, (logo_x, logo_y), logo)
        else:
            draw.text((logo_x, logo_y), logo_text, fill=logo_text_color, font=logo_font, anchor="lt")

        # --- THUMBS + TIME --- #
        for i, img_data in enumerate(screenshots_for_sheet):
            col = i % columns
            row = i // columns

            x = margin + col * (thumbnail_width + margin)
            y = header_height + margin + row * (thumbnail_height + margin)

            img_path = img_data["local_file"]
            full_img_path = self.base_dir / "tmp" / meta["uuid"] / img_path

            try:
                with Image.open(full_img_path) as img:
                    img_resized = img.resize((thumbnail_width, thumbnail_height), Image.Resampling.LANCZOS)
                    sheet.paste(img_resized, (x, y))

                    ss_time = img_data.get("ss_time", 0)
                    hours = int(ss_time // 3600)
                    minutes = int((ss_time % 3600) // 60)
                    secs = int(ss_time % 60)
                    time_str = f"{hours:02d}:{minutes:02d}:{secs:02d}" if hours > 0 else f"{minutes:02d}:{secs:02d}"

                    timestamp_font_size = 12
                    try:
                        timestamp_font = (
                            ImageFont.truetype(font_header.font.name, timestamp_font_size)
                            if hasattr(font_header, "font")
                            else font_meta
                        )
                    except (OSError, ValueError, AttributeError):
                        timestamp_font = font_meta

                    bbox = draw.textbbox((0, 0), time_str, font=timestamp_font, anchor="lt")
                    text_width = bbox[2] - bbox[0]
                    text_height = bbox[3] - bbox[1]

                    padding = 8
                    extra_overflow_padding = 2
                    box_padding_x = padding + extra_overflow_padding
                    box_padding_y = padding + extra_overflow_padding

                    rect_x1 = x + thumbnail_width - text_width - box_padding_x
                    rect_y1 = y + thumbnail_height - text_height - box_padding_y
                    rect_x2 = x + thumbnail_width - extra_overflow_padding
                    rect_y2 = y + thumbnail_height - extra_overflow_padding

                    box_width = max(text_width + 2 * extra_overflow_padding, rect_x2 - rect_x1)
                    box_height = max(text_height + 2 * extra_overflow_padding, rect_y2 - rect_y1)
                    rect_x2 = rect_x1 + box_width
                    rect_y2 = rect_y1 + box_height

                    # Center text within the box
                    text_x = rect_x1 + (box_width - text_width) // 2
                    text_y = rect_y1 + (box_height - text_height) // 2

                    # Background (80% opacity)
                    overlay = Image.new("RGBA", (thumbnail_width, thumbnail_height), (0, 0, 0, 0))
                    overlay_draw = ImageDraw.Draw(overlay)
                    overlay_draw.rectangle([rect_x1 - x, rect_y1 - y, rect_x2 - x, rect_y2 - y], fill=(0, 0, 0, 204))
                    sheet.paste(overlay, (x, y), overlay)
                    draw.text((text_x, text_y), time_str, fill=(255, 255, 255), font=timestamp_font, anchor="lt")

            except Exception as e:
                log.error(f"Error processing thumbnail {img_path}: {e}")
                draw.rectangle([x, y, x + thumbnail_width, y + thumbnail_height], fill=(50, 50, 50))
                draw.text((x + 10, y + thumbnail_height // 2), "ERR", fill=(255, 0, 0))

        # --- SAVE --- #
        try:
            sheet.save(output_path, "PNG", optimize=True, compress_level=6)
            log.debug(f"Contact sheet saved to {output_path}")
        except Exception as e:
            log.error(f"Failed to save contact sheet {output_path}: {e}")
            raise
