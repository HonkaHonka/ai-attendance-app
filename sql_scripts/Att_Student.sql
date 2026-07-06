
SET ANSI_NULLS ON
GO

SET QUOTED_IDENTIFIER ON
GO

CREATE TABLE [dbo].[Att_Student](
	[iSerial] [int] IDENTITY(1,1) NOT NULL,
	[StudentID] [varchar](50) NULL,
	[StudentName] [varchar](100) NULL,
	[Gender] [varchar](50) NULL,
	[College] [varchar](50) NULL,
	[Major] [varchar](100) NULL,
	[Campus] [varchar](50) NULL,
	[StImage] [image] NULL,
	[Created] [datetime] NULL,
	[CreatedBy] [varchar](50) NULL,
	[Updated] [datetime] NULL,
	[UpdatedBy] [varchar](50) NULL,
 CONSTRAINT [PK_Att_Student] PRIMARY KEY CLUSTERED 
(
	[iSerial] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON, OPTIMIZE_FOR_SEQUENTIAL_KEY = OFF) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
GO


